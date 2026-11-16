# Analyzing Mesh Multi-Hop Network Performance

Dietrich Lachmann and Yang Han.

## Use Visual Studio 2022

Open this folder using **File > Open > Folder** in Visual Studio 2022. It opens directly as a CMake project. In Visual Studio Installer, the **Linux and embedded development with C++** workload provides WSL build and debugging support. If presets do not appear, enable **Tools > Options > CMake > General > Always use CMakePresets.json**, then reopen the folder.

The repository includes `.vsconfig` declaring the Windows C++ and Linux C++ workloads. Accept Visual Studio's missing-components prompt, or open **Visual Studio Installer > Modify > Workloads**, select **Linux and embedded development with C++**, and apply the change. Having Ubuntu installed in WSL does not install Visual Studio's Linux integration. Both the Linux workload and its CMake component are required for the WSL build setup in the IDE.

Select an Ubuntu WSL target in the target-system dropdown, then select **WSL Debug** or **WSL Release**. Build the ns-3 dependency once using the instructions below before configuring the project. Select **gateway-bottleneck** as the startup target and use **Build > Build All**, then **Debug > Start Without Debugging** or F5. GDB must be installed in Ubuntu for F5 debugging (`sudo apt-get install -y gdb`). Editing, IntelliSense, builds, and debugging stay in Visual Studio; its WSL integration runs the Linux tools. See [Microsoft's Visual Studio CMake presets guide](https://learn.microsoft.com/en-us/cpp/build/cmake-presets-vs?view=msvc-170).

This workspace also has a private `CMakeUserPresets.json` named **WSL Local Debug (existing Ubuntu 18.04)**. It selects the isolated newer compiler and CMake downloaded for this machine, so you can use the existing **Ubuntu-18.04** target. This machine-specific preset is ignored by Git. The shared WSL presets use the normal compiler on a newer Ubuntu installation. The presets use Visual Studio's mounted-drive WSL toolset to share the same `.deps` installation and project files without copying the dependency tree.

The shared presets invoke `scripts/cmake`, which also detects the newer workspace toolchain when it is present. The companion `scripts/ctest` supplies Visual Studio's test discovery tool. This lets **WSL Debug** work on this machine as well. A missing startup target usually means CMake configuration has not succeeded yet; check **Output > CMake** before changing the executable name in `launch.vs.json`.

If the toolbar shows **Local Machine / x64 Debug**, Visual Studio has selected a Windows build. Enable **Tools > Options > CMake > General > Always use CMakePresets.json**, close and reopen the folder, then select **Ubuntu-18.04** and **WSL Local Debug (existing Ubuntu 18.04)**. The Linux ns-3 installation contains `/mnt/c/...` include and library paths, which cause cascading errors when imported by a Windows build. Keep Windows and WSL builds in separate output folders; the provided presets already do this. CMake now stops early with a setup message if a Windows build is selected.

For saved center and corner launch configurations, copy `docs/launch.vs.json` to `.vs/launch.vs.json`. A copy is already prepared in this workspace. The two entries appear in the startup dropdown after CMake configures. If your Visual Studio version displays a longer target name, generate a configuration via **Debug > Debug and Launch Settings** and retain its generated `projectTarget` value while copying the desired `args` from the example. Results from these launch profiles appear under the active preset's `out/build/<preset>/results` directory. Change the output name before repeating a saved run.

## Build the dependency on Windows using Ubuntu in WSL

Use Ubuntu 24.04 in WSL, or a Linux system with GCC 10.1 or newer, Python 3.8 or newer, CMake 3.20 or newer, Git, and Make. GCC 13 on Ubuntu 24.04 is the intended build environment. This project uses C++20 and pins ns-3.45 for reproducibility. Your currently installed Ubuntu 18.04 environment has GCC 7.5 and needs a newer toolchain. If needed, run this in an Administrator PowerShell terminal to install an additional Ubuntu distribution:

```powershell
wsl --install -d Ubuntu-24.04
wsl -d Ubuntu-24.04
```

In the Ubuntu terminal, install prerequisites, enter the project folder, and build:

```bash
sudo apt-get update
sudo apt-get install -y build-essential cmake git python3
cd /mnt/c/Users/dietr/source/repos/network_simulation_project
bash scripts/bootstrap.sh
ctest --test-dir build --output-on-failure
```

The first build downloads and builds ns-3 inside `.deps`, then builds `build/gateway-bottleneck`. Dependencies and generated results are ignored by Git. No system-wide ns-3 installation is made. The downloaded release is checked against commit `4059af204636298c061df368e241941fa3ff97bf`. Adjust `JOBS` to control build parallelism, for example `JOBS=4 bash scripts/bootstrap.sh`. On Linux you can also clone this repository into your home directory and use the same commands from its root.

If ns-3.45 is already installed, configure against that installation directly:

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH=/path/to/ns3/install
cmake --build build --parallel 2
```

After editing the simulation, rebuild with `cmake --build build --parallel 2`.

## Run the gateway experiment

Run the baseline and a matched corner case from the project root:

```bash
./build/gateway-bottleneck --gridSize=3 --gateway=center --output=results/3x3-center
./build/gateway-bottleneck --gridSize=3 --gateway=corner --output=results/3x3-corner
```

Run a larger grid or change the offered load:

```bash
./build/gateway-bottleneck --gridSize=5 --gateway=center --rateKbps=256 --run=2 --output=results/5x5-center-run2
./build/gateway-bottleneck --PrintHelp
```

Center placement requires an odd grid size so there is one exact center node. Node IDs increase across rows. The default is a 3 by 3 grid with 8 sources, 1024-byte UDP payloads at 128 kbps per source, 30 seconds of routing warmup, 60 seconds of traffic, and 10 seconds to drain queued packets. All sources start together after warmup. These startup choices are part of the model; confirm convergence and inspect startup effects before drawing conclusions for larger grids. Use a fresh output directory for each run; existing result CSVs are protected against overwriting.

For a repeatable center/corner sweep over 3 by 3, 5 by 5, and 7 by 7 grids, with five runs of each case:

```bash
python3 scripts/run_sweep.py --sizes 3 5 7 --runs 5 --output results/sweep-128kbps
```

The sweep writes one result directory per case and a `combined-summary.csv` containing all runs. To explore saturation, repeat with different `--rate-kbps` values and fresh output roots. Keep spacing, radio settings, traffic, and routing parameters fixed when comparing placement. Constant per-source load means aggregate offered traffic increases with the number of sources; a separate experiment holding aggregate offered load fixed helps distinguish load growth from topology effects. Treat each simulation run as an experimental replicate and report variation or confidence intervals across runs. A bottleneck is a hypothesis to evaluate; the setup does not assume that the gateway must be the limiting resource.

## Read the results

`summary.csv` contains the configuration, aggregate gateway throughput, weighted mean delay, delivery and loss ratios, and Jain fairness. `per-source.csv` includes every source, including sources with zero delivery. `topology.csv` records node positions, IP addresses, and the gateway flag. The sweep combines summaries for subsequent plotting in a spreadsheet or analysis tool.

Throughput is received UDP payload bits during the active traffic interval divided by that interval, reported in decimal Mbps. Payload byte counts include the 12-byte sequence/timestamp header and exclude UDP, IP, and Wi-Fi headers. Packets arriving during the drain interval count toward delivery and delay but do not increase active-interval throughput. The offered `rateKbps` uses the same UDP payload size; packets are sent at fixed intervals, so finite windows can differ slightly from the nominal rate. This throughput measures application delivery at the gateway, rather than the configured radio link rate.

Delivery ratio is packets received through the end of draining divided by application send attempts. The undelivered count is attempts minus receptions, and loss ratio is one minus delivery ratio. Undelivered packets can include packets still queued at simulation termination; increase `--drain` and check whether results change before interpreting that count as permanent packet loss. End-to-end delay is measured from the source timestamp to gateway reception and is averaged over delivered packets. A blank delay means no packets arrived. Jain fairness is `(sum of source throughputs)^2 / (source count * sum of squared source throughputs)`, includes zero-throughput sources, and is blank when every source has zero throughput.

Add `--pcap=1` for per-device Wi-Fi packet captures or `--flowXml=1` for optional IP-level FlowMonitor output. FlowMonitor uses different byte accounting and loss semantics from the application CSVs. Routing overhead, MAC retransmissions, route recovery, and observed hop counts are future measurements; the current CSVs cover the gateway portion of the proposal.

The CTest check exercises low-load delivery from every source for both gateway placements, including a four-hop corner path, checks CSV accounting, verifies a disconnected topology reports zero delivery, and checks input and overwrite protection. GitHub Actions builds and runs these checks on Ubuntu 24.04 after the project is pushed.

The official [ns-3 CMake manual](https://www.nsnam.org/docs/release/3.45/manual/html/working-with-cmake.html), [installation guide](https://www.nsnam.org/docs/release/3.45/installation/html/index.html), and [FlowMonitor documentation](https://www.nsnam.org/docs/release/3.45/models/html/flow-monitor.html) describe the underlying build and measurement tools.
