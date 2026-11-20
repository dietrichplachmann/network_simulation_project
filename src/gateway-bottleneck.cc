#include "ns3/aodv-module.h"
#include "ns3/applications-module.h"
#include "ns3/core-module.h"
#include "ns3/dsdv-module.h"
#include "ns3/flow-monitor-module.h"
#include "ns3/internet-module.h"
#include "ns3/mobility-module.h"
#include "ns3/network-module.h"
#include "ns3/olsr-helper.h"
#include "ns3/point-to-point-module.h"
#include "ns3/traffic-control-module.h"
#include "ns3/wifi-module.h"

#include <algorithm>
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
struct Settings
{
    uint32_t gridSize = 3, packetSize = 1024, seed = 1;
    uint32_t wifiQueuePackets = 100, uplinkQueuePackets = 100;
    uint64_t run = 1;
    std::string gateway = "center", routing = "OLSR", output = "results/baseline";
    double spacing = 100, range = 0, warmup = 60, duration = 60, drain = 15;
    double rateKbps = 128, uplinkMbps = 1, uplinkDelayMs = 5, sampleInterval = 1;
    bool pcap = false, flowXml = false;
    double Start() const { return warmup; }
    double Stop() const { return warmup + duration; }
    double End() const { return Stop() + drain; }
};

std::ofstream Csv(const std::filesystem::path& path)
{
    std::ofstream file(path);
    file.exceptions(std::ios::badbit | std::ios::failbit);
    file << std::setprecision(12);
    return file;
}

struct QueueStats
{
    const Settings* cfg = nullptr;
    uint32_t current = 0, peak = 0;
    uint64_t drops = 0, windowDrops = 0;
    double last = 0, windowArea = 0, activeArea = 0;

    void Advance(double now)
    {
        windowArea += current * (now - last);
        activeArea += current * std::max(0.0, std::min(now, cfg->Stop()) -
                                                 std::max(last, cfg->Start()));
        last = now;
    }
    void Changed(uint32_t, uint32_t value)
    {
        const double now = Simulator::Now().GetSeconds();
        Advance(now);
        current = value;
        if (now >= cfg->Start() && now < cfg->Stop()) peak = std::max(peak, value);
    }
    void Drop()
    {
        if (Simulator::Now().GetSeconds() >= cfg->Start())
        {
            ++drops;
            ++windowDrops;
        }
    }
};

void WifiDrop(QueueStats* stats, Ptr<const WifiMpdu>) { stats->Drop(); }
void WireDrop(QueueStats* stats, Ptr<const Packet>) { stats->Drop(); }

struct NodeStats
{
    uint64_t sent = 0, received = 0, activeRxBytes = 0;
    uint64_t windowSent = 0, windowReceived = 0, windowRxBytes = 0;
    uint64_t forwardedPackets = 0, windowForwardedBytes = 0;
    double delaySum = 0, windowDelaySum = 0;
    QueueStats queue;
};

void CountTx(NodeStats* stats, Ptr<const Packet>)
{
    ++stats->sent;
    ++stats->windowSent;
}

struct Measurements
{
    const Settings& cfg;
    uint32_t gatewayId;
    Ipv4Address serverAddress;
    std::vector<NodeStats> nodes;
    std::map<Ipv4Address, uint32_t> byAddress;
    QueueStats uplink;
    std::ofstream times, nodeTimes;
    double previousSample;
    double wireActiveBusySeconds = 0;
    uint64_t windowWireBytes = 0, gatewayForwardPackets = 0;

    Measurements(const Settings& settings, uint32_t gatewayNode, uint32_t count,
                 const std::filesystem::path& out)
        : cfg(settings), gatewayId(gatewayNode), nodes(count),
          times(Csv(out / "timeseries.csv")), nodeTimes(Csv(out / "node-timeseries.csv")),
          previousSample(settings.Start())
    {
        uplink.cfg = &cfg;
        for (auto& node : nodes) node.queue.cfg = &cfg;
        times << "time_s,traffic_time_s,phase,interval_s,offered_mbps,server_throughput_mbps,"
                 "gateway_forward_mbps,mean_delay_ms,rx_packets,uplink_queue_mean_packets,"
                 "uplink_queue_packets,uplink_queue_drops,wire_tx_mbps\n";
        nodeTimes << "time_s,node_id,wifi_queue_mean_packets,wifi_queue_packets,"
                     "wifi_queue_drops,forward_mbps,source_rx_mbps\n";
    }

    void Receive(Ptr<const Packet> packet, const Address& from)
    {
        const auto source = InetSocketAddress::ConvertFrom(from).GetIpv4();
        const auto found = byAddress.find(source);
        if (found == byAddress.end()) return;
        auto& stats = nodes.at(found->second);
        SeqTsHeader header;
        packet->PeekHeader(header);
        const double delay = (Simulator::Now() - header.GetTs()).GetSeconds();
        ++stats.received;
        ++stats.windowReceived;
        stats.windowRxBytes += packet->GetSize();
        stats.delaySum += delay;
        stats.windowDelaySum += delay;
        if (Simulator::Now().GetSeconds() < cfg.Stop()) stats.activeRxBytes += packet->GetSize();
    }

    void Forward(uint32_t node, const Ipv4Header& header, Ptr<const Packet> packet, uint32_t)
    {
        if (header.GetDestination() != serverAddress || header.GetProtocol() != 17 ||
            Simulator::Now().GetSeconds() < cfg.Start()) return;
        ++nodes[node].forwardedPackets;
        nodes[node].windowForwardedBytes += packet->GetSize() + header.GetSerializedSize();
        if (node == gatewayId) ++gatewayForwardPackets;
    }

    void WireTransmit(Ptr<const Packet> packet)
    {
        const double now = Simulator::Now().GetSeconds();
        const double finish = now + packet->GetSize() * 8.0 / (cfg.uplinkMbps * 1e6);
        wireActiveBusySeconds += std::max(0.0, std::min(finish, cfg.Stop()) -
                                                  std::max(now, cfg.Start()));
        if (now >= cfg.Start()) windowWireBytes += packet->GetSize();
    }

    void Sample()
    {
        const double now = Simulator::Now().GetSeconds();
        const double elapsed = now - previousSample;
        uint64_t tx = 0, rx = 0, bytes = 0;
        double delay = 0;
        uplink.Advance(now);
        for (uint32_t i = 0; i < nodes.size(); ++i)
        {
            auto& node = nodes[i];
            node.queue.Advance(now);
            if (elapsed > 0)
            {
                nodeTimes << now << ',' << i << ',' << node.queue.windowArea / elapsed << ','
                          << node.queue.current << ',' << node.queue.windowDrops << ','
                          << node.windowForwardedBytes * 8.0 / elapsed / 1e6 << ','
                          << node.windowRxBytes * 8.0 / elapsed / 1e6 << '\n';
            }
            tx += node.windowSent;
            rx += node.windowReceived;
            bytes += node.windowRxBytes;
            delay += node.windowDelaySum;
        }
        if (elapsed > 0)
        {
            times << now << ',' << now - cfg.Start() << ','
                  << (previousSample < cfg.Stop() ? "traffic" : "drain") << ',' << elapsed << ','
                  << tx * cfg.packetSize * 8.0 / elapsed / 1e6 << ','
                  << bytes * 8.0 / elapsed / 1e6 << ','
                  << nodes[gatewayId].windowForwardedBytes * 8.0 / elapsed / 1e6 << ',';
            if (rx) times << delay * 1000.0 / rx;
            times << ',' << rx << ',' << uplink.windowArea / elapsed << ',' << uplink.current
                  << ',' << uplink.windowDrops << ',' << windowWireBytes * 8.0 / elapsed / 1e6 << '\n';
        }
        for (auto& node : nodes)
        {
            node.windowSent = node.windowReceived = node.windowRxBytes = 0;
            node.windowForwardedBytes = 0;
            node.windowDelaySum = node.queue.windowArea = 0;
            node.queue.windowDrops = 0;
        }
        uplink.windowArea = 0;
        uplink.windowDrops = 0;
        windowWireBytes = 0;
        previousSample = now;
        if (now < cfg.End() - 1e-8)
        {
            // Include the traffic-stop boundary exactly, even for fractional durations.
            double next = std::min(now + cfg.sampleInterval, cfg.End());
            if (now < cfg.Stop() - 1e-8) next = std::min(next, cfg.Stop());
            Simulator::Schedule(Seconds(next - now), &Measurements::Sample, this);
        }
    }
};

void ForwardTrace(Measurements* data, uint32_t node, const Ipv4Header& header,
                  Ptr<const Packet> packet, uint32_t iface)
{
    data->Forward(node, header, packet, iface);
}
} // namespace

int main(int argc, char* argv[])
{
    Settings cfg;
    CommandLine cmd(__FILE__);
    cmd.AddValue("gridSize", "n in the n by n mesh grid", cfg.gridSize);
    cmd.AddValue("gateway", "center (nearest grid node, lower-right tie) or corner", cfg.gateway);
    cmd.AddValue("routing", "AODV, OLSR, or DSDV", cfg.routing);
    cmd.AddValue("spacing", "Grid spacing in meters", cfg.spacing);
    cmd.AddValue("range", "Radio cutoff; 0 selects 1.1 times spacing", cfg.range);
    cmd.AddValue("warmup", "Routing warmup seconds (AODV still discovers data routes on demand)", cfg.warmup);
    cmd.AddValue("duration", "Traffic generation seconds", cfg.duration);
    cmd.AddValue("drain", "Seconds to drain after traffic stops", cfg.drain);
    cmd.AddValue("rateKbps", "UDP payload kbps offered by each source", cfg.rateKbps);
    cmd.AddValue("packetSize", "UDP payload bytes including 12-byte timestamp", cfg.packetSize);
    cmd.AddValue("uplinkMbps", "Gateway external link capacity in decimal Mbps", cfg.uplinkMbps);
    cmd.AddValue("uplinkDelayMs", "External link one-way propagation delay", cfg.uplinkDelayMs);
    cmd.AddValue("uplinkQueuePackets", "Finite external DropTail queue capacity", cfg.uplinkQueuePackets);
    cmd.AddValue("wifiQueuePackets", "Per-node Wi-Fi MAC queue capacity", cfg.wifiQueuePackets);
    cmd.AddValue("sampleInterval", "Time-series interval in seconds", cfg.sampleInterval);
    cmd.AddValue("seed", "Random seed", cfg.seed);
    cmd.AddValue("run", "Independent random run", cfg.run);
    cmd.AddValue("pcap", "Write Wi-Fi and uplink captures", cfg.pcap);
    cmd.AddValue("flowXml", "Write optional IP FlowMonitor report", cfg.flowXml);
    cmd.AddValue("output", "Fresh experiment output directory", cfg.output);
    cmd.Parse(argc, argv);
    try
    {
        const auto positive = [](double x) { return std::isfinite(x) && x > 0; };
        if (cfg.gridSize < 2 || cfg.gridSize > 255 ||
            (cfg.gateway != "center" && cfg.gateway != "corner") ||
            (cfg.routing != "AODV" && cfg.routing != "OLSR" && cfg.routing != "DSDV") ||
            !positive(cfg.spacing) || !std::isfinite(cfg.range) || cfg.range < 0 ||
            !positive(cfg.warmup) || !positive(cfg.duration) || !positive(cfg.drain) ||
            !positive(cfg.rateKbps) || !positive(cfg.uplinkMbps) ||
            !std::isfinite(cfg.uplinkDelayMs) || cfg.uplinkDelayMs < 0 ||
            !positive(cfg.sampleInterval) || !cfg.wifiQueuePackets || !cfg.uplinkQueuePackets ||
            cfg.packetSize < 12 || cfg.packetSize > 1472 || !cfg.seed ||
            cfg.seed >= 2147483647 || !cfg.run || cfg.output.empty())
            throw std::invalid_argument("Invalid parameters; use --PrintHelp.");
        cfg.range = cfg.range == 0 ? 1.1 * cfg.spacing : cfg.range;
        const Time interval = Seconds(cfg.packetSize * 8.0 / (cfg.rateKbps * 1000.0));
        if (interval.IsZero()) throw std::invalid_argument("Traffic rate exceeds time resolution.");
        const std::filesystem::path out(cfg.output);
        if (std::filesystem::exists(out) && !std::filesystem::is_empty(out))
            throw std::invalid_argument("Output directory is not empty; choose a fresh directory.");
        std::filesystem::create_directories(out);
        RngSeedManager::SetSeed(cfg.seed);
        RngSeedManager::SetRun(cfg.run);
        Config::SetDefault("ns3::WifiMacQueue::MaxSize",
                           QueueSizeValue(QueueSize(std::to_string(cfg.wifiQueuePackets) + "p")));
        const uint32_t count = cfg.gridSize * cfg.gridSize;
        const uint32_t gatewayId = cfg.gateway == "corner" ? 0 :
            (cfg.gridSize / 2) * cfg.gridSize + cfg.gridSize / 2;
        NodeContainer mesh, server, all;
        mesh.Create(count);
        server.Create(1);
        all.Add(mesh);
        all.Add(server);
        MobilityHelper mobility;
        mobility.SetPositionAllocator("ns3::GridPositionAllocator", "MinX", DoubleValue(0),
            "MinY", DoubleValue(0), "DeltaX", DoubleValue(cfg.spacing),
            "DeltaY", DoubleValue(cfg.spacing), "GridWidth", UintegerValue(cfg.gridSize),
            "LayoutType", StringValue("RowFirst"));
        mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
        mobility.Install(mesh);
        YansWifiChannelHelper channel;
        channel.SetPropagationDelay("ns3::ConstantSpeedPropagationDelayModel");
        channel.AddPropagationLoss("ns3::RangePropagationLossModel", "MaxRange", DoubleValue(cfg.range));
        YansWifiPhyHelper phy;
        phy.SetChannel(channel.Create());
        WifiHelper wifi;
        wifi.SetStandard(WIFI_STANDARD_80211a);
        wifi.SetRemoteStationManager("ns3::ConstantRateWifiManager", "DataMode",
            StringValue("OfdmRate6Mbps"), "ControlMode", StringValue("OfdmRate6Mbps"));
        WifiMacHelper mac;
        mac.SetType("ns3::AdhocWifiMac");
        const auto devices = wifi.Install(phy, mac, mesh);
        int64_t stream = wifi.AssignStreams(devices, 0);
        PointToPointHelper wire;
        wire.SetDeviceAttribute("DataRate", DataRateValue(DataRate(uint64_t(cfg.uplinkMbps * 1e6))));
        wire.SetChannelAttribute("Delay", TimeValue(Seconds(cfg.uplinkDelayMs / 1000.0)));
        wire.SetQueue("ns3::DropTailQueue<Packet>", "MaxSize",
                      QueueSizeValue(QueueSize(std::to_string(cfg.uplinkQueuePackets) + "p")));
        // Use exactly one finite egress queue; no additional traffic-control backlog.
        wire.DisableFlowControl();
        const auto wired = wire.Install(mesh.Get(gatewayId), server.Get(0));
        InternetStackHelper internet;
        OlsrHelper olsr;
        AodvHelper aodv;
        DsdvHelper dsdv;
        if (cfg.routing == "OLSR") internet.SetRoutingHelper(olsr);
        else if (cfg.routing == "AODV") internet.SetRoutingHelper(aodv);
        else internet.SetRoutingHelper(dsdv);
        // The selected protocol also runs on the external server. It learns the
        // server route over the wired link; no ideal global routes bypass the mesh.
        internet.Install(all);
        if (cfg.routing == "OLSR") olsr.AssignStreams(all, stream);
        else if (cfg.routing == "AODV") aodv.AssignStreams(all, stream);
        else for (auto node = all.Begin(); node != all.End(); ++node)
            stream += (*node)->GetObject<dsdv::RoutingProtocol>()->AssignStreams(stream);
        Ipv4AddressHelper addresses;
        addresses.SetBase("10.1.0.0", "255.255.0.0");
        const auto meshIfaces = addresses.Assign(devices);
        addresses.SetBase("10.2.0.0", "255.255.255.252");
        const auto wiredIfaces = addresses.Assign(wired);
        TrafficControlHelper trafficControl;
        for (uint32_t i = 0; i < wired.GetN(); ++i)
        {
            auto device = wired.Get(i);
            auto layer = device->GetNode()->GetObject<TrafficControlLayer>();
            if (layer->GetRootQueueDiscOnDevice(device)) trafficControl.Uninstall(device);
        }
        Measurements data(cfg, gatewayId, count, out);
        data.serverAddress = wiredIfaces.GetAddress(1);
        for (uint32_t i = 0; i < count; ++i)
        {
            data.byAddress.emplace(meshIfaces.GetAddress(i), i);
            auto queue = DynamicCast<WifiNetDevice>(devices.Get(i))->GetMac()->GetTxop()->GetWifiMacQueue();
            queue->TraceConnectWithoutContext("PacketsInQueue", MakeCallback(&QueueStats::Changed, &data.nodes[i].queue));
            queue->TraceConnectWithoutContext("Drop", MakeBoundCallback(&WifiDrop, &data.nodes[i].queue));
            mesh.Get(i)->GetObject<Ipv4L3Protocol>()->TraceConnectWithoutContext(
                "UnicastForward", MakeBoundCallback(&ForwardTrace, &data, i));
        }
        auto gatewayDevice = DynamicCast<PointToPointNetDevice>(wired.Get(0));
        gatewayDevice->GetQueue()->TraceConnectWithoutContext("PacketsInQueue", MakeCallback(&QueueStats::Changed, &data.uplink));
        gatewayDevice->GetQueue()->TraceConnectWithoutContext("Drop", MakeBoundCallback(&WireDrop, &data.uplink));
        gatewayDevice->TraceConnectWithoutContext("PhyTxBegin", MakeCallback(&Measurements::WireTransmit, &data));
        PacketSinkHelper sink("ns3::UdpSocketFactory", InetSocketAddress(Ipv4Address::GetAny(), 9000));
        auto sinkApps = sink.Install(server);
        sinkApps.Start(Seconds(0));
        sinkApps.Get(0)->TraceConnectWithoutContext("Rx", MakeCallback(&Measurements::Receive, &data));
        Ptr<UniformRandomVariable> phase = CreateObject<UniformRandomVariable>();
        phase->SetStream(100000);
        for (uint32_t i = 0; i < count; ++i)
        {
            if (i == gatewayId) continue;
            UdpClientHelper client(data.serverAddress, 9000);
            client.SetAttribute("MaxPackets", UintegerValue(0));
            client.SetAttribute("PacketSize", UintegerValue(cfg.packetSize));
            client.SetAttribute("Interval", TimeValue(interval));
            auto apps = client.Install(mesh.Get(i));
            apps.Get(0)->TraceConnectWithoutContext("Tx", MakeBoundCallback(&CountTx, &data.nodes[i]));
            // Avoid an artificial collision burst from identical source phases.
            apps.Start(Seconds(cfg.Start() + phase->GetValue(0, std::min(1.0, interval.GetSeconds()))));
            apps.Stop(Seconds(cfg.Stop()));
        }
        FlowMonitorHelper flowHelper;
        Ptr<FlowMonitor> monitor;
        if (cfg.flowXml) monitor = flowHelper.InstallAll();
        if (cfg.pcap)
        {
            phy.EnablePcapAll((out / "wifi").string());
            wire.EnablePcapAll((out / "external").string());
        }
        Simulator::Schedule(Seconds(cfg.Start()), &Measurements::Sample, &data);
        Simulator::Stop(Seconds(cfg.End() + 1e-6));
        Simulator::Run();
        auto topology = Csv(out / "topology.csv");
        topology << "node_id,ip,x_m,y_m,is_gateway,role\n";
        auto sources = Csv(out / "per-source.csv");
        sources << "node_id,source_ip,tx_packets,rx_packets,undelivered_packets,delivery_ratio,"
                   "loss_ratio,throughput_mbps,mean_delay_ms,wifi_queue_mean_packets,wifi_queue_peak_packets,wifi_queue_drops\n";
        uint64_t sent = 0, received = 0, wifiDrops = 0;
        double totalThroughput = 0, sumSquares = 0, delaySum = 0;
        for (uint32_t i = 0; i < count; ++i)
        {
            const auto pos = mesh.Get(i)->GetObject<MobilityModel>()->GetPosition();
            topology << i << ',' << meshIfaces.GetAddress(i) << ',' << pos.x << ',' << pos.y
                     << ',' << (i == gatewayId) << ',' << (i == gatewayId ? "gateway" : "mesh") << '\n';
            wifiDrops += data.nodes[i].queue.drops;
            if (i == gatewayId) continue;
            const auto& stats = data.nodes[i];
            const double throughput = stats.activeRxBytes * 8.0 / cfg.duration / 1e6;
            const double delivery = stats.sent ? double(stats.received) / stats.sent : 0;
            sources << i << ',' << meshIfaces.GetAddress(i) << ',' << stats.sent << ',' << stats.received
                    << ',' << stats.sent - stats.received << ',' << delivery << ',' << 1 - delivery
                    << ',' << throughput << ',';
            if (stats.received) sources << stats.delaySum * 1000.0 / stats.received;
            sources << ',' << stats.queue.activeArea / cfg.duration << ',' << stats.queue.peak
                    << ',' << stats.queue.drops << '\n';
            sent += stats.sent;
            received += stats.received;
            delaySum += stats.delaySum;
            totalThroughput += throughput;
            sumSquares += throughput * throughput;
        }
        topology << count << ',' << data.serverAddress << ',' << cfg.gridSize * cfg.spacing
                 << ",0,0,server\n";
        auto summary = Csv(out / "summary.csv");
        summary << "grid_size,node_count,source_count,gateway,gateway_id,routing,spacing_m,range_m,"
                   "rate_kbps,packet_size_bytes,warmup_s,duration_s,drain_s,seed,run,tx_packets,"
                   "rx_packets,undelivered_packets,delivery_ratio,loss_ratio,gateway_throughput_mbps,"
                   "mean_delay_ms,jain_fairness,uplink_mbps,uplink_delay_ms,uplink_queue_capacity,"
                   "wifi_queue_capacity,sample_interval_s,uplink_queue_mean_packets,uplink_queue_peak_packets,"
                   "uplink_queue_drops,wifi_queue_drops,uplink_utilization,gateway_forward_packets,server_ip\n";
        const double delivery = sent ? double(received) / sent : 0;
        summary << cfg.gridSize << ',' << count << ',' << count - 1 << ',' << cfg.gateway << ','
                << gatewayId << ',' << cfg.routing << ',' << cfg.spacing << ',' << cfg.range << ','
                << cfg.rateKbps << ',' << cfg.packetSize << ',' << cfg.warmup << ',' << cfg.duration
                << ',' << cfg.drain << ',' << cfg.seed << ',' << cfg.run << ',' << sent << ','
                << received << ',' << sent - received << ',' << delivery << ',' << 1 - delivery << ','
                << totalThroughput << ',';
        if (received) summary << delaySum * 1000.0 / received;
        summary << ',';
        if (sumSquares > 0) summary << totalThroughput * totalThroughput / ((count - 1) * sumSquares);
        summary << ',' << cfg.uplinkMbps << ',' << cfg.uplinkDelayMs << ',' << cfg.uplinkQueuePackets
                << ',' << cfg.wifiQueuePackets << ',' << cfg.sampleInterval << ','
                << data.uplink.activeArea / cfg.duration << ',' << data.uplink.peak << ','
                << data.uplink.drops << ',' << wifiDrops << ',' << data.wireActiveBusySeconds / cfg.duration
                << ',' << data.gatewayForwardPackets << ',' << data.serverAddress << '\n';
        if (monitor) monitor->SerializeToXmlFile((out / "flow-monitor.xml").string(), true, true);
        std::cout << cfg.gridSize << 'x' << cfg.gridSize << ' ' << cfg.gateway << ' ' << cfg.routing
                  << " run " << cfg.run << ": " << totalThroughput << " Mbps, delivery " << delivery
                  << ", uplink drops " << data.uplink.drops << "; " << cfg.output << '\n';
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
