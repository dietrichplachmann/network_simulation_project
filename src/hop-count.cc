#include "ns3/applications-module.h"
#include "ns3/core-module.h"
// #include "ns3/aodv-helper.h"
// #include "ns3/dsdv-helper.h"
#include "ns3/internet-module.h"
#include "ns3/mobility-module.h"
#include "ns3/network-module.h"
#include "ns3/olsr-helper.h"
#include "ns3/wifi-module.h"

#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>

using namespace ns3;

namespace
{
constexpr uint16_t port = 9000;

struct Measurements
{
    uint64_t sent = 0;
    uint64_t received = 0;
    uint64_t activeRxBytes = 0;
    uint64_t retryAttempts = 0;
    double delaySumSeconds = 0;
    Time trafficStop;
    Ipv4Address destination;

    void Send(Ptr<const Packet>) { ++sent; }

    void Receive(Ptr<const Packet> packet, const Address&)
    {
        SeqTsHeader header;
        packet->PeekHeader(header);
        ++received;
        delaySumSeconds += (Simulator::Now() - header.GetTs()).GetSeconds();
        if (Simulator::Now() < trafficStop)
        {
            activeRxBytes += packet->GetSize();
        }
    }

    // Observe actual over-the-air retries, including attempts on relay nodes.
    void ObserveTx(Ptr<const Packet> packet, uint16_t, WifiTxVector, MpduInfo, uint16_t)
    {
        WifiMacHeader mac;
        auto frame = packet->Copy();
        frame->RemoveHeader(mac);
        if (!mac.IsData() || !mac.IsRetry())
        {
            return;
        }

        LlcSnapHeader llc;
        frame->RemoveHeader(llc);
        if (llc.GetType() != 0x0800)
        {
            return;
        }

        Ipv4Header ip;
        frame->RemoveHeader(ip);
        if (ip.GetDestination() != destination || ip.GetProtocol() != 17)
        {
            return;
        }

        UdpHeader udp;
        frame->PeekHeader(udp);
        if (udp.GetDestinationPort() == port)
        {
            ++retryAttempts;
        }
    }
};

std::ofstream OpenCsv(const std::filesystem::path& path)
{
    std::ofstream file(path);
    file.exceptions(std::ios::badbit | std::ios::failbit);
    file << std::setprecision(12);
    return file;
}
} // namespace

int main(int argc, char* argv[])
{
    uint32_t hops = 1;
    std::string protocol = "OLSR";
    double spacing = 100;
    double warmup = 30;
    double duration = 60;
    double drain = 10;
    double rateKbps = 128;
    uint32_t packetSize = 1024;
    uint32_t seed = 1;
    uint64_t run = 1;
    bool pcap = false;
    std::string output = "results/line-baseline";

    CommandLine cmd(__FILE__);
    cmd.AddValue("hops", "Number of wireless links (nodes = hops + 1)", hops);
    cmd.AddValue("protocol", "Routing protocol: OLSR", protocol);
    cmd.AddValue("spacing", "Distance between adjacent nodes in meters", spacing);
    cmd.AddValue("warmup", "Seconds before starting the source", warmup);
    cmd.AddValue("duration", "Seconds of source traffic", duration);
    cmd.AddValue("drain", "Seconds after traffic stops to deliver queued packets", drain);
    cmd.AddValue("rateKbps", "Offered UDP payload rate per source in decimal kbps", rateKbps);
    cmd.AddValue("packetSize", "UDP payload bytes, including 12-byte timestamp header", packetSize);
    cmd.AddValue("seed", "ns-3 random seed", seed);
    cmd.AddValue("run", "Independent random run number", run);
    cmd.AddValue("pcap", "Write per-device packet captures", pcap);
    cmd.AddValue("output", "Output directory; use a unique directory per run", output);
    cmd.Parse(argc, argv);

    try
    {
        const auto positive = [](double x) { return std::isfinite(x) && x > 0; };

        if (hops < 1 || hops > 254 ||
            protocol != "OLSR" ||
            !positive(spacing) || !positive(warmup) || !positive(duration) || !positive(drain) ||
            !positive(rateKbps) || packetSize < 12 || packetSize > 1472 || seed == 0 ||
            seed >= 2147483647 || run == 0 || output.empty())
        {
            throw std::invalid_argument("Invalid arguments. Use --PrintHelp.");
        }

        const double range = 1.1 * spacing;
        const Time interval = Seconds(packetSize * 8.0 / (rateKbps * 1000.0));
        if (interval.IsZero())
        {
            throw std::invalid_argument("rateKbps is too high for the simulator time resolution.");
        }

        const std::filesystem::path out(output);
        std::filesystem::create_directories(out);
        if (std::filesystem::exists(out / "summary.csv"))
        {
            throw std::invalid_argument("Results already exist. Choose a new --output directory.");
        }

        RngSeedManager::SetSeed(seed);
        RngSeedManager::SetRun(run);

        const uint32_t count = hops + 1;
        const uint32_t sinkId = hops;
        const double trafficStop = warmup + duration;
        const double simulationStop = trafficStop + drain;

        NodeContainer nodes;
        nodes.Create(count);

        MobilityHelper mobility;
        mobility.SetPositionAllocator("ns3::GridPositionAllocator",
                                      "MinX", DoubleValue(0),
                                      "MinY", DoubleValue(0),
                                      "DeltaX", DoubleValue(spacing),
                                      "DeltaY", DoubleValue(spacing),
                                      "GridWidth", UintegerValue(count),
                                      "LayoutType", StringValue("RowFirst"));
        mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
        mobility.Install(nodes);

        YansWifiChannelHelper channel;
        channel.SetPropagationDelay("ns3::ConstantSpeedPropagationDelayModel");
        channel.AddPropagationLoss("ns3::RangePropagationLossModel",
                                   "MaxRange", DoubleValue(range));

        YansWifiPhyHelper phy;
        phy.SetChannel(channel.Create());

        WifiHelper wifi;
        wifi.SetStandard(WIFI_STANDARD_80211a);
        wifi.SetRemoteStationManager("ns3::ConstantRateWifiManager",
                                     "DataMode", StringValue("OfdmRate6Mbps"),
                                     "ControlMode", StringValue("OfdmRate6Mbps"));

        WifiMacHelper mac;
        mac.SetType("ns3::AdhocWifiMac");

        NetDeviceContainer devices = wifi.Install(phy, mac, nodes);
        wifi.AssignStreams(devices, 0);

        InternetStackHelper internet;
        // AodvHelper aodv;
        OlsrHelper olsr;
        // DsdvHelper dsdv;

        // if (protocol == "AODV")
        // {
        //     internet.SetRoutingHelper(aodv);
        // }

        if (protocol == "OLSR")
        {
            internet.SetRoutingHelper(olsr);
        }

        // if (protocol == "DSDV")
        // {
        //     internet.SetRoutingHelper(dsdv);
        // }

        internet.Install(nodes);

        Ipv4AddressHelper addresses;
        addresses.SetBase("10.1.0.0", "255.255.0.0");
        Ipv4InterfaceContainer interfaces = addresses.Assign(devices);

        Measurements measurements;
        measurements.trafficStop = Seconds(trafficStop);
        measurements.destination = interfaces.GetAddress(sinkId);

        for (auto it = devices.Begin(); it != devices.End(); ++it)
        {
            DynamicCast<WifiNetDevice>(*it)->GetPhy()->TraceConnectWithoutContext(
                "MonitorSnifferTx", MakeCallback(&Measurements::ObserveTx, &measurements));
        }

        PacketSinkHelper sink("ns3::UdpSocketFactory",
                              InetSocketAddress(Ipv4Address::GetAny(), port));
        auto sinkApps = sink.Install(nodes.Get(sinkId));
        sinkApps.Start(Seconds(0));
        sinkApps.Get(0)->TraceConnectWithoutContext(
            "Rx", MakeCallback(&Measurements::Receive, &measurements));

        UdpClientHelper client(measurements.destination, port);
        client.SetAttribute("MaxPackets", UintegerValue(0));
        client.SetAttribute("PacketSize", UintegerValue(packetSize));
        client.SetAttribute("Interval", TimeValue(interval));

        auto source = client.Install(nodes.Get(0));
        source.Get(0)->TraceConnectWithoutContext(
            "Tx", MakeCallback(&Measurements::Send, &measurements));
        source.Start(Seconds(warmup));
        source.Stop(Seconds(trafficStop));

        if (pcap)
        {
            phy.EnablePcapAll((out / "wifi").string());
        }

        Simulator::Stop(Seconds(simulationStop));
        Simulator::Run();

        auto summary = OpenCsv(out / "summary.csv");
        summary << "protocol,hops,intermediate_nodes,nodes,run,seed,spacing_m,range_m,"
                   "rate_kbps,packet_size_bytes,warmup_s,duration_s,drain_s,tx_packets,"
                   "rx_packets,throughput_mbps,mean_delay_ms,delivery_ratio,wifi_retries\n";

        summary << protocol << ',' << hops << ',' << hops - 1 << ',' << count << ','
                << run << ',' << seed << ',' << spacing << ',' << range << ',' << rateKbps
                << ',' << packetSize << ',' << warmup << ',' << duration << ',' << drain
                << ',' << measurements.sent << ',' << measurements.received << ','
                << measurements.activeRxBytes * 8.0 / duration / 1e6 << ',';

        if (measurements.received)
        {
            summary << measurements.delaySumSeconds * 1000 / measurements.received;
        }

        summary << ',' << double(measurements.received) / measurements.sent << ','
                << measurements.retryAttempts << '\n';

        std::cout << protocol << ", " << hops << " hops: " << measurements.received << "/"
                  << measurements.sent << " delivered; " << out << '\n';

        Simulator::Destroy();
        return 0;
    }
    catch (const std::exception& error)
    {
        std::cerr << "Error: " << error.what() << '\n';
        Simulator::Destroy();
        return 1;
    }
}