kjdragan@DESKTOP-9EOUS3M:/mnt/c/Users/kevin/repos$ HOST=api.anthropic.com
NS ==="
timekjdragan@DESKTOP-9EOUS3M:/mnt/c/Users/kevin/repos$
kjdragan@DESKTOP-9EOUS3M:/mnt/c/Users/kevin/repos$ echo "=== DNS ==="
hosts $=== DNS ===
kjdragan@DESKTOP-9EOUS3M:/mnt/c/Users/kevin/repos$ time getent hosts $HOST >/dev/null
ho "=== CURL timing (HTTPS) ==="
curl -sS -o /dev/null \
  -w $'namelookup: %{time_namelookup}\nconnect:    %{time_connect}\nappconnect: %{time_appconnect}\npretransfer:%{time_pretransfer}\nstartxfer:  %{time_starttransfer}\ntotal:      %{time_total}\n' \
  https://$HOST/

real    0m0.048s
user    0m0.000s
sys     0m0.004s
kjdragan@DESKTOP-9EOUS3M:/mnt/c/Users/kevin/repos$
kjdragan@DESKTOP-9EOUS3M:/mnt/c/Users/kevin/repos$ echo "=== CURL timing (HTTPS) ==="
=== CURL timing (HTTPS) ===
kjdragan@DESKTOP-9EOUS3M:/mnt/c/Users/kevin/repos$ curl -sS -o /dev/null \
>   -w $'namelookup: %{time_namelookup}\nconnect:    %{time_connect}\nappconnect: %{time_appconnect}\npretransfer:%{time_pretransfer}\nstartxfer:  %{time_starttransfer}\ntotal:      %{time_total}\n' \
>   https://$HOST/
namelookup: 0.043603
connect:    0.050025
appconnect: 0.088625
pretransfer:0.088802
startxfer:  0.136928
total:      0.136982
kjdragan@DESKTOP-9EOUS3M:/mnt/c/Users/kevin/repos$ echo "=== IPv4 only ==="
l -4 -sS=== IPv4 only ===
 -o /dekjdragan@DESKTOP-9EOUS3M:/mnt/c/Users/kevin/repos$ curl -4 -sS -o /dev/null -w "total: %{time_total}\n" https://api.anthropic.com/
cho "=== IPv6 only ==="
curl -6 -sS -o /dev/null -w "total: %{time_total}\n" https://api.anthropic.com/
total: 0.097859
kjdragan@DESKTOP-9EOUS3M:/mnt/c/Users/kevin/repos$
kjdragan@DESKTOP-9EOUS3M:/mnt/c/Users/kevin/repos$ echo "=== IPv6 only ==="
=== IPv6 only ===
kjdragan@DESKTOP-9EOUS3M:/mnt/c/Users/kevin/repos$ curl -6 -sS -o /dev/null -w "total: %{time_total}\n" https://api.anthropic.com/
curl: (7) Failed to connect to api.anthropic.com port 443 after 4 ms: Couldn't connect to server
total: 0.004982
kjdragan@DESKTOP-9EOUS3M:/mnt/c/Users/kevin/repos$
network
