#include "ns3/applications-module.h"
#include "ns3/core-module.h"
#include "ns3/flow-monitor-module.h"
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
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

using namespace ns3;

namespace
{
struct SourceStats
{
    uint64_t sent = 0;
    uint64_t received = 0;
    uint64_t activeRxBytes = 0;
    double delaySumSeconds = 0;
};

void CountTx(SourceStats* stats, Ptr<const Packet>)
{
    // Count every application attempt, including failures before IP routing.
    ++stats->sent;
}

struct Measurements
{
    std::vector<SourceStats> sources;
    std::map<Ipv4Address, uint32_t> nodeByAddress;
    double trafficStop = 0;

    void Receive(Ptr<const Packet> packet, const Address& from)
    {
        const auto address = InetSocketAddress::ConvertFrom(from).GetIpv4();
        auto& stats = sources.at(nodeByAddress.at(address));
        SeqTsHeader header;
        packet->PeekHeader(header);
        ++stats.received;
        stats.delaySumSeconds += (Simulator::Now() - header.GetTs()).GetSeconds();
        if (Simulator::Now().GetSeconds() < trafficStop)
        {
            stats.activeRxBytes += packet->GetSize();
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
    uint32_t gridSize = 3;
    std::string gateway = "center";
    double spacing = 100;
    double range = 0; // zero selects 1.1 * spacing, excluding diagonals
    double warmup = 30;
    double duration = 60;
    double drain = 10;
    double rateKbps = 128;
    uint32_t packetSize = 1024;
    uint32_t seed = 1;
    uint64_t run = 1;
    bool pcap = false;
    bool flowXml = false;
    std::string output = "results/baseline";

    CommandLine cmd(__FILE__);
    cmd.AddValue("gridSize", "Nodes per side; center requires an odd size", gridSize);
    cmd.AddValue("gateway", "Gateway location: center or corner (node 0)", gateway);
    cmd.AddValue("spacing", "Horizontal and vertical spacing in meters", spacing);
    cmd.AddValue("range", "Idealized radio cutoff in meters; 0 selects 1.1 * spacing", range);
    cmd.AddValue("warmup", "Seconds for OLSR convergence before traffic", warmup);
    cmd.AddValue("duration", "Seconds of source traffic", duration);
    cmd.AddValue("drain", "Seconds after traffic stops to deliver queued packets", drain);
    cmd.AddValue("rateKbps", "Offered UDP payload rate per source in decimal kbps", rateKbps);
    cmd.AddValue("packetSize", "UDP payload bytes, including 12-byte timestamp header", packetSize);
    cmd.AddValue("seed", "ns-3 random seed", seed);
    cmd.AddValue("run", "Independent random run number", run);
    cmd.AddValue("pcap", "Write per-device packet captures", pcap);
    cmd.AddValue("flowXml", "Write additional IP-level FlowMonitor XML", flowXml);
    cmd.AddValue("output", "Output directory; use a unique directory per run", output);
    cmd.Parse(argc, argv);

    try
    {
        const auto positive = [](double x) { return std::isfinite(x) && x > 0; };
        if (gridSize < 2 || gridSize > 255 ||
            (gateway != "center" && gateway != "corner") ||
            (gateway == "center" && gridSize % 2 == 0) ||
            !positive(spacing) || !std::isfinite(range) || range < 0 ||
            !positive(warmup) || !positive(duration) || !positive(drain) ||
            !positive(rateKbps) || packetSize < 12 || packetSize > 1472 || seed == 0 ||
            seed >= 2147483647 || run == 0 || output.empty())
        {
            throw std::invalid_argument("Invalid parameters. Use --PrintHelp for parameter descriptions. "
                                        "Center gateways require an odd grid size >= 3.");
        }
        range = range == 0 ? 1.1 * spacing : range;
        const Time interval = Seconds(packetSize * 8.0 / (rateKbps * 1000.0));
        if (interval.IsZero())
        {
            throw std::invalid_argument("rateKbps is too high for the simulator time resolution.");
        }
        const std::filesystem::path out(output);
        std::filesystem::create_directories(out);
        if (std::filesystem::exists(out / "summary.csv") ||
            std::filesystem::exists(out / "per-source.csv"))
        {
            throw std::invalid_argument("Results already exist. Choose a new --output directory.");
        }
        RngSeedManager::SetSeed(seed);
        RngSeedManager::SetRun(run);

        const uint32_t count = gridSize * gridSize;
        const uint32_t gatewayId = gateway == "corner" ? 0 : count / 2;
        const double trafficStop = warmup + duration;
        const double simulationStop = trafficStop + drain;
        NodeContainer nodes;
        nodes.Create(count);

        MobilityHelper mobility;
        mobility.SetPositionAllocator("ns3::GridPositionAllocator",
                                      "MinX", DoubleValue(0), "MinY", DoubleValue(0),
                                      "DeltaX", DoubleValue(spacing),
                                      "DeltaY", DoubleValue(spacing),
                                      "GridWidth", UintegerValue(gridSize),
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
        const int64_t wifiStreams = wifi.AssignStreams(devices, 0);

        OlsrHelper olsr;
        InternetStackHelper internet;
        internet.SetRoutingHelper(olsr);
        internet.Install(nodes);
        olsr.AssignStreams(nodes, wifiStreams);
        Ipv4AddressHelper addresses;
        addresses.SetBase("10.1.0.0", "255.255.0.0");
        Ipv4InterfaceContainer interfaces = addresses.Assign(devices);

        Measurements measurements;
        measurements.sources.resize(count);
        measurements.trafficStop = trafficStop;
        for (uint32_t i = 0; i < count; ++i)
        {
            measurements.nodeByAddress.emplace(interfaces.GetAddress(i), i);
        }
        constexpr uint16_t port = 9000;
        PacketSinkHelper sink("ns3::UdpSocketFactory",
                              InetSocketAddress(Ipv4Address::GetAny(), port));
        auto sinkApps = sink.Install(nodes.Get(gatewayId));
        sinkApps.Start(Seconds(0));
        sinkApps.Get(0)->TraceConnectWithoutContext(
            "Rx", MakeCallback(&Measurements::Receive, &measurements));

        for (uint32_t i = 0; i < count; ++i)
        {
            if (i == gatewayId)
            {
                continue;
            }
            UdpClientHelper client(interfaces.GetAddress(gatewayId), port);
            client.SetAttribute("MaxPackets", UintegerValue(0)); // unlimited
            client.SetAttribute("PacketSize", UintegerValue(packetSize));
            client.SetAttribute("Interval", TimeValue(interval));
            auto apps = client.Install(nodes.Get(i));
            apps.Get(0)->TraceConnectWithoutContext(
                "Tx", MakeBoundCallback(&CountTx, &measurements.sources[i]));
            apps.Start(Seconds(warmup));
            apps.Stop(Seconds(trafficStop));
        }
        if (pcap)
        {
            phy.EnablePcapAll((out / "wifi").string());
        }
        FlowMonitorHelper flowHelper;
        Ptr<FlowMonitor> monitor;
        if (flowXml)
        {
            monitor = flowHelper.InstallAll();
        }
        Simulator::Stop(Seconds(simulationStop));
        Simulator::Run();

        auto topology = OpenCsv(out / "topology.csv");
        topology << "node_id,ip,x_m,y_m,is_gateway\n";
        auto perSource = OpenCsv(out / "per-source.csv");
        perSource << "node_id,source_ip,tx_packets,rx_packets,undelivered_packets,"
                     "delivery_ratio,loss_ratio,throughput_mbps,mean_delay_ms\n";
        uint64_t sent = 0;
        uint64_t received = 0;
        double delaySum = 0;
        double totalThroughput = 0;
        double sumSquares = 0;
        for (uint32_t i = 0; i < count; ++i)
        {
            const auto pos = nodes.Get(i)->GetObject<MobilityModel>()->GetPosition();
            topology << i << ',' << interfaces.GetAddress(i) << ',' << pos.x << ',' << pos.y
                     << ',' << (i == gatewayId) << '\n';
            if (i == gatewayId)
            {
                continue;
            }
            const auto& stats = measurements.sources[i];
            const double throughput = stats.activeRxBytes * 8.0 / duration / 1e6;
            const double delivery = stats.sent ? double(stats.received) / stats.sent : 0;
            perSource << i << ',' << interfaces.GetAddress(i) << ',' << stats.sent << ','
                      << stats.received << ',' << stats.sent - stats.received << ',' << delivery
                      << ',' << 1 - delivery << ',' << throughput << ',';
            if (stats.received)
            {
                perSource << stats.delaySumSeconds * 1000.0 / stats.received;
            }
            perSource << '\n';
            sent += stats.sent;
            received += stats.received;
            delaySum += stats.delaySumSeconds;
            totalThroughput += throughput;
            sumSquares += throughput * throughput;
        }
        auto summary = OpenCsv(out / "summary.csv");
        summary << "grid_size,node_count,source_count,gateway,gateway_id,routing,spacing_m,"
                   "range_m,rate_kbps,packet_size_bytes,warmup_s,duration_s,drain_s,seed,run,"
                   "tx_packets,rx_packets,undelivered_packets,delivery_ratio,loss_ratio,"
                   "gateway_throughput_mbps,mean_delay_ms,jain_fairness\n";
        const double delivery = sent ? double(received) / sent : 0;
        summary << gridSize << ',' << count << ',' << count - 1 << ',' << gateway << ','
                << gatewayId << ",OLSR," << spacing << ',' << range << ',' << rateKbps << ','
                << packetSize << ',' << warmup << ',' << duration << ',' << drain << ','
                << seed << ',' << run << ',' << sent << ',' << received << ','
                << sent - received << ',' << delivery << ',' << 1 - delivery << ','
                << totalThroughput << ',';
        if (received)
        {
            summary << delaySum * 1000.0 / received;
        }
        summary << ',';
        if (sumSquares > 0)
        {
            summary << totalThroughput * totalThroughput / ((count - 1) * sumSquares);
        }
        summary << '\n';
        if (monitor)
        {
            monitor->CheckForLostPackets();
            monitor->SerializeToXmlFile((out / "flow-monitor.xml").string(), true, true);
        }
        std::cout << count << " nodes; " << gateway << " gateway; throughput = "
                  << totalThroughput << " Mbps; delivery = " << delivery
                  << "; results: " << out.string() << '\n';
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
