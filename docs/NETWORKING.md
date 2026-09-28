# TURN and network strategy

Research checkpoint: 2026-09-28.

## Goal

TURN is a reliability fallback, not the preferred media path. WebRTC should use a direct or server-reflexive path when it works and use a relay when NAT/firewall conditions require one.

Both peers need usable ICE configuration. OmniLive therefore applies the same dynamic ICE provider to Android and the backend aiortc PeerConnection.

Android now resolves the nominated candidate pair from WebRTC stats and records local/remote candidate type, transport protocol, relay/direct state, candidate RTT, available outgoing bitrate, and actual video send/receive bitrate. Candidate IP addresses are intentionally unnecessary for routine benchmarking.

## Option A: self-host coturn near Nigerian users

AWS currently lists the Nigeria (Lagos) Local Zone:
- zone: `af-south-1-los-1a`
- network border group: `af-south-1-los-1`
- parent region: `af-south-1`

Source:
https://docs.aws.amazon.com/local-zones/latest/ug/available-local-zones.html

This is the most useful self-hosted latency benchmark target because it lets us compare a Lagos relay against managed Anycast TURN on the actual mobile networks.

Recommended coturn authentication mode:

```text
fingerprint
use-auth-secret
static-auth-secret=<same value as OMNILIVE_TURN_SHARED_SECRET>
realm=<turn hostname>
listening-port=3478
tls-listening-port=5349
min-port=49152
max-port=65535
```

Open the configured listener ports and the configured UDP relay range in the instance/network firewall. Add a valid certificate/private key before relying on `turns:` TLS.

Coturn documents TURN REST shared-secret authentication as a timestamped username plus base64 HMAC password:
https://github.com/coturn/coturn/blob/master/README.turnserver

Listener reference:
https://github.com/coturn/coturn/blob/master/man/man1/turnserver.1

Example OmniLive config:

```text
OMNILIVE_ICE_URLS=stun:stun.cloudflare.com:3478,turn:turn.example.com:3478?transport=udp,turns:turn.example.com:5349?transport=tcp
OMNILIVE_TURN_SHARED_SECRET=<long random shared secret>
OMNILIVE_TURN_CREDENTIAL_TTL_SECONDS=900
OMNILIVE_TURN_USER=omnilive
```

## Option B: Cloudflare Realtime TURN

Cloudflare Realtime TURN is Anycast, so no regional TURN deployment/load balancer is required. Current published pricing is USD 0.05/GB with a 1,000 GB free tier, and Cloudflare STUN is free/unlimited.

Sources:
https://developers.cloudflare.com/realtime/
https://developers.cloudflare.com/realtime/turn/faq/

Published service addresses:
- STUN UDP: `stun.cloudflare.com:3478`
- TURN UDP/TCP: `turn.cloudflare.com:3478`
- TURN TLS: `turn.cloudflare.com:5349`

Cloudflare requires its TURN key to remain server-side and uses an API to mint short-lived credentials:
https://developers.cloudflare.com/realtime/turn/generate-credentials/

OmniLive does not hardcode Cloudflare's credential API yet. If Cloudflare wins the latency/reliability benchmark, add it as another ICE credential provider rather than replacing the current generic coturn/static providers.

## Option C: Twilio Network Traversal Service

Twilio remains a viable managed fallback. Its current public TURN pricing page lists relays in US West, Germany, Singapore, Mumbai, Tokyo, Sydney, and São Paulo. It does not list an Africa TURN site on that page, and Europe is currently USD 0.40/GB.

Source:
https://www.twilio.com/en-us/stun-turn/pricing

For the Nigeria-first latency objective, benchmark it rather than assuming Germany will beat Anycast or a Lagos relay.

## Benchmark order

For each target network, record:
- nominated local/remote ICE candidate type
- candidate protocol and relay/direct state
- WebRTC candidate-pair RTT
- packet loss
- actual video send/receive kbps
- target bitrate and available outgoing bitrate
- encoder FPS and sent-frame count
- frame-correlated capture-to-return p50/p95 and correlation misses
- backend input age p50/p95
- processor p50/p95 and model p50/p95
- returned frames/FPS
- reconnection time after Wi-Fi/cellular transition

Run at least:
- Nigerian cellular network A
- Nigerian cellular network B if available
- Wi-Fi
- deliberately restrictive/NAT-heavy connection if available

Do not pick the production TURN topology from provider marketing or geography alone; use the measurements.
