# Yamaha Spotify Bridge

> ⚠️ **This script was generated with AI assistance and may contain bugs. It works on the author's setup, but use it at your own risk.**

> This is a proof of concept. The goal was to prove that cross-VLAN Spotify Connect for Yamaha devices can work, and to document how and why. The script works, but it is far from production-ready. It likely has edge cases, error handling gaps, and things that could be done better by someone with deeper knowledge of networking and Python. If you know what you are doing, please feel free to use the work and improve it — you are very welcome. The author lacks the expertise to take this further, and hopes this saves someone else the hours of troubleshooting that went into figuring it out.

A lightweight Python script for OpenWrt routers that acts as a Spotify Connect proxy between a guest Wi-Fi network and a Yamaha MusicCast receiver (or any other Spotify Connect device) located on the main LAN.

## The Problem

Spotify Connect requires the phone and the target device to be on the same subnet. If your Yamaha receiver is on `192.168.1.x` (LAN) and your guest devices are on `192.168.2.x` (guest network), Spotify will not show the receiver — even if firewall rules allow traffic between the subnets.

## How It Works

The script runs two components simultaneously on the router:

**HTTP Proxy** — listens on the router's guest IP (e.g. `192.168.2.1:80`) and forwards all requests to the Yamaha receiver on the LAN (`192.168.1.220:80`). From Spotify's perspective, the device appears to be inside the guest subnet.

**mDNS announcer** — advertises the bridge as a `_spotify-connect._tcp` service on the guest network. It both sends proactive announcements every 5 seconds and responds to incoming mDNS queries from phones.

```
Phone (192.168.2.x)
    │
    │  mDNS discovery
    ▼
Router bridge (192.168.2.1:80)   ← script listens here
    │
    │  HTTP proxy
    ▼
Yamaha (192.168.1.220:80)        ← real device
    │
    │  Spotify cloud connection
    ▼
Spotify servers
```

## Author's Network Setup

The setup described in this project is based on the following network configuration. Your IP addresses and interface names may differ, but the general structure should be the same.

The guest Wi-Fi was configured following the official OpenWrt guide: https://openwrt.org/docs/guide-user/network/wifi/guestwifi/configuration_webinterface

### Interfaces (`Network → Interfaces`)

| Interface | Bridge | Protocol | IPv4 address |
|-----------|--------|----------|--------------|
| `lan` | `br-lan` | Static | `192.168.1.1/24` |
| `guest` | `br-guest` | Static | `192.168.2.1/24` |

Both interfaces have DHCP server enabled. The Yamaha receiver is connected to the LAN and gets an address in `192.168.1.0/24`.

### Firewall zones (`Network → Firewall → Zones`)

| Zone | Input | Output | Forward | Masquerading | Forwards to |
|------|-------|--------|---------|--------------|-------------|
| `lan` | accept | accept | accept | no | `wan` |
| `wan` | reject | accept | reject | yes | — |
| `guest` | reject | accept | reject | no | `wan` |

Key points: the `guest` zone has no forwarding to `lan`, so guest devices cannot reach anything on the main LAN by default. The bridge script adds a single controlled exception via a firewall rule.

### Firewall rules (`Network → Firewall → Traffic Rules`)

The following rules are relevant to the guest network:

| Rule | From | To | Protocol | Port | Action |
|------|------|----|----------|------|--------|
| Allow-DNS-Guest | guest | this device | UDP/TCP | 53 | accept |
| Allow-DHCP-Guest | guest | this device | UDP | 67 | accept |
| guest-spotify-bridge | guest | this device (`192.168.2.1`) | TCP | 80 | accept |

The `guest-spotify-bridge` rule is the only addition required by this project. It allows guest devices to reach the bridge script running on the router's guest IP. No direct access to the LAN or to `192.168.1.220` is granted to guest devices.

### LuCI web interface

By default, LuCI listens on all interfaces including the guest network (port 80). To prevent guest devices from accessing the router admin panel, restrict it to LAN only:

```sh
uci set uhttpd.main.listen_http='192.168.1.1:80'
uci set uhttpd.main.listen_https='192.168.1.1:443'
uci commit uhttpd
service uhttpd restart
```

This also frees up port 80 on the guest interface so the bridge script can use it.

## Requirements

- OpenWrt router with Python 3 installed
- Router must have an IP on both LAN and guest networks
- Yamaha receiver (or other Spotify Connect device) on the main LAN
- No external Python packages required — only the standard library

## Installation

### 1. Install Python 3

```sh
opkg update && opkg install python3
```

### 2. Copy the script to the router

```sh
scp spotify-bridge.py root@192.168.1.1:/usr/bin/
```

### 3. Edit configuration

Open `/usr/bin/spotify-bridge.py` and adjust the variables at the top:

```python
YAMAHA_IP   = "192.168.1.220"   # IP of your Yamaha receiver
YAMAHA_PORT = 80

BRIDGE_IP   = "192.168.2.1"    # Router IP on the guest network
BRIDGE_PORT = 80               # Port to listen on (must be 80)

INSTANCE_NAME = "SpotifyBridge" # mDSN instance name
```

### 4. Free up port 80 on the guest interface

By default, LuCI (the OpenWrt web UI) listens on all interfaces including the guest network. Restrict it to LAN only:

```sh
uci set uhttpd.main.listen_http='192.168.1.1:80'
uci set uhttpd.main.listen_https='192.168.1.1:443'
uci commit uhttpd
service uhttpd restart
```

### 5. Open port 80 in the firewall for guest → router

```sh
uci add firewall rule
uci set firewall.@rule[-1].name='guest-spotify-bridge'
uci set firewall.@rule[-1].src='guest'
uci set firewall.@rule[-1].dest_ip='192.168.2.1'
uci set firewall.@rule[-1].proto='tcp'
uci set firewall.@rule[-1].dest_port='80'
uci set firewall.@rule[-1].target='ACCEPT'
uci commit firewall
service firewall restart
```

### 6. Test manually

```sh
python3 /usr/bin/spotify-bridge.py
```

Open Spotify on a phone connected to the guest network. The Yamaha receiver should appear in the device list within a few seconds.

## Running as a Service

To have the bridge start automatically on boot:

### Create the init script

```sh
cat > /etc/init.d/spotify-bridge << 'EOF'
#!/bin/sh /etc/rc.common

START=99
STOP=10
USE_PROCD=1

start_service() {
    procd_open_instance
    procd_set_param command python3 /usr/bin/spotify-bridge.py
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_set_param respawn 300 5 0
    procd_close_instance
}
EOF

chmod +x /etc/init.d/spotify-bridge
```

`respawn 300 5 0` means procd will automatically restart the script if it crashes.

### Enable and start

```sh
service spotify-bridge enable
service spotify-bridge start
```

### Service management

```sh
service spotify-bridge start    # start
service spotify-bridge stop     # stop
service spotify-bridge restart  # restart
service spotify-bridge status   # check status
```

### View logs

```sh
logread | grep spotify-bridge
```

## Security

The script only proxies HTTP traffic to one specific IP and port (the Yamaha receiver). Guest devices have no access to the rest of the LAN — the OpenWrt firewall continues to block all other traffic. The only intentional "gap" is access to the receiver itself.

## Tested on

- Router: GL.iNet GL-MT6000 (Flint 2) running OpenWrt 24.10.1
- Receiver: Yamaha R-N803D (MusicCast)
- Clients: Android

## What Was Tried Before This Solution

This script came out of an extensive troubleshooting session. Here is a summary of what was attempted, why it seemed promising, and why it did not work — so you don't have to repeat the same steps.

### avahi (mDNS reflector)

**What it does:** avahi in reflector mode copies mDNS packets between network interfaces, so devices on the guest network can see mDNS announcements from the LAN.

**Why it didn't work:** The Yamaha R-N803D does not advertise itself via mDNS as `_spotify-connect._tcp`. Running `avahi-browse -a -t` on the router confirmed this — the receiver was completely absent from mDNS. avahi can only relay what is already being announced, so there was nothing to relay.

### multicast-relay

**What it does:** [multicast-relay](https://github.com/alsmith/multicast-relay) is a Python script that relays both SSDP and mDNS packets between interfaces. It correctly forwarded SSDP packets from the Yamaha to the guest network, confirmed via verbose logs.

**Why it didn't work:** The Yamaha's SSDP announcements only advertise `upnp:rootdevice` and `MediaRenderer:1` — there is no Spotify Connect service type in any of the SSDP messages. Spotify does not use SSDP to discover Connect devices on Yamaha hardware. Phones on the guest network received the SSDP packets but Spotify ignored them.

### Opening firewall ports (guest → Yamaha)

**What was tried:** Opening all ports recommended in the Yamaha documentation for Spotify Connect: `TCP 80, TCP 49154, TCP 51000, UDP 1900, UDP 5353, UDP 51100, UDP 51200, UDP 61100`, as well as a broader range `UDP 32768–61000`.

**Why it didn't work:** The firewall was never the core issue. `tcpdump` confirmed that when the phone was on the guest network, it never even attempted to connect to `192.168.1.220` — because Spotify hid the device before any TCP connection was made. Ports don't help if the device is not shown in the first place.

### IP address mapping via nftables (192.168.2.220 → 192.168.1.220)

**What was tried:** Adding a virtual IP `192.168.2.220` on the guest bridge interface and creating nftables DNAT rules to redirect traffic from that address to the real Yamaha IP:

```sh
ip addr add 192.168.2.220/32 dev br-guest
nft add table ip nat
nft add chain ip nat prerouting { type nat hook prerouting priority -100 \; }
nft add chain ip nat postrouting { type nat hook postrouting priority 100 \; }
nft add rule ip nat prerouting ip daddr 192.168.2.220 dnat to 192.168.1.220
nft add rule ip nat postrouting ip daddr 192.168.1.220 masquerade
```

The HTTP endpoint at `192.168.2.220/goform/spotifyConfig` responded correctly — verified from the phone's browser. However, Spotify still did not show the receiver.

**Why it didn't work:** Spotify receives the receiver's real IP (`192.168.1.220`) from its cloud backend and checks that specific address for local reachability. It has no knowledge of `192.168.2.220`, so the virtual address was never queried by the Spotify app.

### Root cause: how Spotify Connect actually works on Yamaha

`tcpdump` on the LAN interface revealed the actual discovery mechanism. When a phone on the LAN opens Spotify, within milliseconds it sends:

```
GET /goform/spotifyConfig?action=getInfo&version=2.12.0 HTTP/1.1
Host: 192.168.1.220
```

Spotify gets the receiver's IP from its cloud backend (the receiver maintains a persistent TCP connection to Spotify servers on port 4070). The app then performs a local reachability check against that IP on port 80. If the IP is not reachable from the phone's current subnet, the device is silently hidden from the Connect device list.

This is why all proxy and relay approaches at the network layer fail — Spotify already knows the LAN IP and checks it directly. The only working solution is to present a completely separate endpoint that lives in the guest subnet and proxies the HTTP conversation to the real receiver.

## References

- [Spotify Connect Zeroconf Troubleshooting](https://github.com/thlucas1/homeassistantcomponent_spotifyplus/wiki/Spotify-Connect-Zeroconf-Troubleshooting) — detailed breakdown of the Spotify Connect Zeroconf API, manufacturer device list, and the `/goform/spotifyConfig` endpoint used by Yamaha
- [multicast-relay](https://github.com/alsmith/multicast-relay) — useful for other cross-VLAN discovery problems (Chromecast, AirPlay, DLNA), just not for Yamaha Spotify Connect
- [OpenWrt forum: Spotify Connect with different VLANs](https://forum.openwrt.org/t/spotify-connect-with-different-vlans-does-not-work-even-with-mdns/169860) — confirms this is a widespread problem with no clean existing solution
