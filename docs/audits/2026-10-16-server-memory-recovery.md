# Server memory recovery

2026-10-16 14:56–14:59 UTC: SSH resumed without reboot (uptime 34 days).
Kernel journal establishes system-wide OOM victims at 14:28:35 (PID1403291,
anon RSS10,742,556kB), 14:34:00 (PID1403111,12,033,008kB), and 14:56:09
(PID1406885,17,581,520kB). Their exact command lines were unavailable after
termination; attributing every victim to wiki discovery would be inference.
The rollout code did materialize 60 days of the 16GB source cache, a confirmed
unbounded memory path requiring repair before activation.

Containment: stopped stock-report-public-wiki.timer; persistent systemd
properties (daemon-reloaded) set public wiki MemoryMax3G/MemorySwapMax0 and
cron.service aggregate MemoryMax8G/MemorySwapMax512M. The cron aggregate limit
may terminate batch jobs rather than allowing host-wide memory exhaustion;
this is containment, not a fix for each caller's memory complexity.

Repeated independent SSH calls succeeded. SSH and tailscaled active. Available
memory16.6GB at14:56:42; memory PSI some avg10 fell39.70→2.47→0.00%; load1
fell295.01→28.43 by14:58:35. Historical load5/load15 remained elevated.
No new kernel OOM line observed after14:56:20 in the inspected interval.
Wiki timer remains stopped pending bounded discovery smoke verification.
