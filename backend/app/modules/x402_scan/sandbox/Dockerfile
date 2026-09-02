# Sandbox image for the x402 file/tarball security-scan endpoint (roadmap
# item 18b). Built once, run per-request via `docker run --rm --network none`
# (see services/sandbox_runner.py) -- this image never talks to the network
# at request time; the ClamAV signature DB is baked in at BUILD time instead.
#
# Alpine, not Debian: this container's whole job is running tools (ClamAV,
# file) against adversarial input, so a minimal base is a real attack-surface
# reduction, not just a size optimization -- ClamAV has had real CVEs where a
# crafted archive triggered memory corruption during parsing; fewer installed
# packages/binaries means less for a future bug like that to land on. scan.py
# is pure Python stdlib (no C-extension pip deps), so musl-vs-glibc
# compatibility risk here is low, and ClamAV-on-Alpine is a well-trodden
# combination.
#
# Rebuild periodically (cron/CI, not wired yet) to refresh the ClamAV DB --
# a stale DB inside a --network none container can never freshclam itself.
#
# Layer split below is deliberate, not cosmetic (NOT a multi-stage build --
# multi-stage `FROM ... AS x` is for discarding build-time artifacts from the
# final image, e.g. compiling in one stage and copying only the binary into a
# slim stage; there's nothing here to discard, so it doesn't apply). Docker
# caches each RUN as its own layer and only invalidates a layer -- and every
# layer after it -- when that instruction's content changes. `apk add` here
# almost never changes, so keeping it in its own RUN lets a daily rebuild
# reuse the cached package-install layer instead of re-resolving/downloading
# every package. freshclam is split into a second RUN specifically so package
# installs and DB refresh don't share one cache key.
#
# That split alone is necessary but NOT sufficient: an unmodified RUN
# instruction is itself a cache hit, so a `docker build` with no other
# changes would reuse the freshclam layer too and silently keep serving a
# stale DB forever -- the exact opposite of the goal. SIG_DB_DATE exists to
# force that one layer to re-run: pass a value that changes daily
# (`--build-arg SIG_DB_DATE=$(date +%Y%m%d)`) and Docker invalidates this RUN
# (and only this RUN onward) whenever the ARG value differs from the last
# build, without touching the apk layer above it.
FROM alpine:3.20

RUN apk add --no-cache ca-certificates clamav clamav-libunrar file python3 shadow \
        yara ssdeep exiftool

ARG SIG_DB_DATE=unknown
RUN echo "clamav signature refresh: ${SIG_DB_DATE}" \
    && timeout 90 freshclam --quiet

# YARA + signature-base (Neo23x0/signature-base, curated apt_*/gen_*/webshell/
# hacktool subset -- see checks/yara_scan.py's module docstring for exactly
# which files and why, and what's deliberately excluded). Own layer, own
# cache key: this only needs to change when the pinned commit is bumped, not
# on every daily SIG_DB_DATE-forced freshclam rebuild above.
RUN mkdir -p /opt/yara-rules/src \
    && wget -qO- https://github.com/Neo23x0/signature-base/archive/6fb4bd73b7eea485a717d19df25eb76587d02573.tar.gz \
       | tar xz -C /opt/yara-rules/src --strip-components=1 \
    && cd /opt/yara-rules/src/yara \
    && rm -f gen_webshells_ext_vars.yar gen_fake_amsi_dll.yar gen_mal_3cx_compromise_mar23.yar \
             gen_susp_obfuscation.yar gen_vcruntime140_dll_sideloading.yar thor_inverse_matches.yar \
             generic_anomalies.yar general_cloaking.yar yara_mixed_ext_vars.yar configured_vulns_ext_vars.yar \
             yara-rules_vuln_drivers_strict_renamed.yar expl_citrix_netscaler_adc_exploitation_cve_2023_3519.yar \
             expl_connectwise_screenconnect_vuln_feb24.yar \
    && ls | grep -E '^(crime_|mal_|expl_|exploit_|vul_|vuln_|yara-rules_)' | xargs -r rm -f \
    && yarac apt_*.yar gen_*.yar thor-webshells.yar thor-hacktools.yar cn_pentestset_webshells.yar \
             general_officemacros.yar \
             /opt/yara-rules/signature-base.yarc \
    && rm -rf /opt/yara-rules/src

RUN useradd --no-create-home --uid 10001 scanner
COPY scan.py /scan.py
COPY checks/ /checks/
RUN chmod 444 /scan.py && chmod -R a-w /checks

# clamd sidecar config only (see ../sandbox/clamd.conf's own docstring). Not
# used by the default ENTRYPOINT/CMD below -- the long-lived clamd sidecar
# container overrides both via start_clamd_sidecar.sh
# (`--entrypoint clamd ... -c /etc/clamav/clamd-sidecar.conf`), and this
# per-request scan path never invokes clamd directly, only clamdscan as a
# client against it (checks/clamav.py). apk already installs clamd/clamdscan
# as clamav-scanner/clamav-clamdscan dependencies of the clamav package
# above -- no new package needed for this.
COPY clamd.conf /etc/clamav/clamd-sidecar.conf
RUN chmod 444 /etc/clamav/clamd-sidecar.conf

USER scanner
WORKDIR /scan
ENTRYPOINT ["python3", "/scan.py"]
CMD ["/scan/input"]
