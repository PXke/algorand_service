# Reddit read-only verification access — research findings (2026-09-05)

## Scope of this research

Question: can we legitimately build a narrow, read-only capability — fetch a
specific, already-known Reddit thread/URL during an already-triggered story,
for a handful of verification lookups per week — without crossing into
"Reddit ingest" (a removed, do-not-restore lane per root `CLAUDE.md`)?

No code was written, no accounts created, no credentials touched. All
findings below are from directly fetching Reddit's own current policy pages
and `robots.txt`, plus live empirical HTTP probes, done in this session on
2026-09-05. Anywhere I could not get a live primary source, I say so
explicitly rather than filling the gap from older training knowledge.

**Note on tooling**: the sandboxed `WebSearch`/`WebFetch` tools were
unavailable this session (search quota exhausted; `WebFetch` couldn't
resolve DNS for anything, including a control fetch to `example.com`). I
fell back to `curl` from Bash, which did have real outbound access to
`reddit.com`/`redditinc.com`/`archive.org`. All quotes below are from pages
fetched this way; raw HTML is saved under `/tmp/reddit_*.html` (not part of
the repo, not committed).

## 1. Official API access model and pricing

**Confirmed, current documents** (fetched directly):

- Data API Terms — `https://www.redditinc.com/policies/data-api-terms`
  ("Effective June 19, 2023. Last Revised July 20, 2026")
- Developer Terms — `https://www.redditinc.com/policies/developer-terms`
  ("Effective September 24, 2024. Last Revised March 24, 2026")
- User Agreement — `https://redditinc.com/policies/user-agreement`
  ("Effective July 1, 2026. Last Revised May 26, 2026")

All three are live, current-dated documents, not stale cached copies.

On pricing, Data API Terms §3.1 (Fees) states, verbatim:

> "Reddit reserves the right to charge fees for future use or access to the
> Data APIs, rates to be determined at Reddit's sole discretion. If you are
> interested in using the Data APIs for commercial purposes, research in
> excess of rate limits, or for any use that is not expressly permitted
> under the Data API Terms, then you will need to enter into a separate
> agreement with Reddit."

Developer Terms §4.1 (Commercial Use Restrictions) is the sharper point for
this project specifically:

> "Reddit reserves the right to charge fees for access and use of Reddit
> Services and Data, rates to be determined at Reddit's sole discretion.
> Unless expressly permitted in the Developer Terms or applicable Additional
> Terms or otherwise approved in writing by us, you will not... access or
> use any of the Reddit Services and Data by or on behalf of a business or
> as part of a service or product that is monetized."

**This is the actual blocker for question 1, not the rate limit.** PXke
Algorand's newspaper is a commercial/monetized product. Per this clause,
routine commercial use of the official API — even at hobby volume, even
just a handful of read calls a week — falls outside whatever free tier
exists unless Reddit separately approves it in writing. I could not find
wording anywhere in these documents carving out a no-cost exception for
"low volume."

**Not confirmed — exact current numeric rate limits / price-per-call.** I
tried multiple official documentation URLs for the actual numbers
(`support.reddithelp.com/hc/en-us/articles/...` for the API wiki and pricing
FAQ, `developers.reddit.com`, `old.reddit.com/dev/api`, `www.reddit.com/wiki/api`).
Every one either:
- returned a Cloudflare JS challenge page ("Just a moment... Enable
  JavaScript and cookies to continue" — confirmed via direct fetch of
  `support.reddithelp.com/hc/en-us/articles/26410290525844-Public-Content-Policy`,
  which robots.txt itself points to as the authoritative policy, see §3), or
- redirected to a login/"Welcome to Reddit" wall (`old.reddit.com/dev/api`), or
- resolved to `developers.reddit.com`, which is the **Devvit** app-hosting
  platform (build interactive apps that run inside Reddit) — a different
  product from the read-only Data API, and not what we need.

So I cannot quote a current $/1000-calls or QPM figure from a live 2026
source. Public reporting from 2023 (the API-pricing controversy) put the
paid tier around $0.24/1000 calls with a free tier capped around 100 QPM
per OAuth client — but that is old-training-knowledge, not something I
re-verified live this session, and Reddit's terms above make clear the
number itself is secondary to the commercial-use gate.

## 2. Does an authenticated real-account script get treated differently?

**Confirmed — no, explicitly.** User Agreement (current, 2026-07-01
effective), in the list of things you may not do:

> "Access, search, or collect data from the Services by any means
> (automated or otherwise) except as permitted in these Terms or in a
> separate agreement with Reddit (we conditionally grant permission to
> crawl the Services in accordance with the parameters set forth in our
> robots.txt file, but scraping the Services without Reddit's prior written
> consent is prohibited)."

And:

> "Use the Services in any manner (automated, including via bots, or
> otherwise) that could interfere with, disable, disrupt, overburden, or
> otherwise impair the Services."

This clause governs the *site* (reddit.com), separately from the Data API
Terms which govern the *API*. It draws no distinction between an anonymous
script and a script running under a real, logged-in human's session — the
prohibition is on the automation itself, gated behind "Reddit's prior
written consent," not on whether the requester is authenticated. So the
`storage_state`-cookie approach this codebase already uses for other gated
sites (`BROWSER_STORAGE_STATE_PATH`, Playwright) does not get a pass here
just because the exported session belongs to a real account — automating
reads through that authenticated session is exactly "scraping the Services
without Reddit's prior written consent," which the User Agreement calls out
by name as prohibited.

Developer Terms §4.2 adds a second, narrower prohibition that bites even
harder for an AI news writer specifically:

> "access or use the Reddit Services and Data through any means (including
> by accessing our API or indexing, caching, or crawling our Reddit
> Services and Data) to train large language, artificial intelligence, or
> other algorithmic models or related services without our permission."

Data API Terms §2.4 has the same idea from the content-licensing side: the
license to display User Content is "solely as necessary to develop, deploy,
distribute, and run your App," and explicitly excludes "any right to use
User Content for other purposes, such as for training a machine learning or
AI model... without the express permission of rightsholders." This project's
writer is an LLM that would read the fetched thread and generate article
text from it — not literally "training a model," but "or related services"
is broad, undefined language that a conservative reading would not want to
test.

## 3. Other live read-only options (RSS/.json, mirrors, Pushshift)

**Confirmed, live and current — `robots.txt` is a blanket disallow.** I
fetched `https://www.reddit.com/robots.txt` directly this session:

```
# Welcome to Reddit's robots.txt
# Reddit believes in an open internet, but not the misuse of public content.
# See https://support.reddithelp.com/hc/en-us/articles/26410290525844-Public-Content-Policy Reddit's Public Content Policy for access and use restrictions to Reddit content.
# See https://www.reddit.com/r/reddit4researchers/ for details on how Reddit continues to support research and non-commercial use.
# policy: https://support.reddithelp.com/hc/en-us/articles/26410290525844-Public-Content-Policy

User-agent: *
Disallow: /
```

`old.reddit.com/robots.txt` likewise returns `User-Agent: * / Disallow: /`.
There is no longer a conditional crawl allowance for any path — the "we
conditionally grant permission to crawl... in accordance with... robots.txt"
carve-out in the User Agreement (quoted above) currently grants nothing,
because robots.txt disallows everything.

**Empirically confirmed, this session, same tooling** — the 403 wall this
project already hit in production is real and reproducible, but
inconsistent rather than a hard 100%-block:
- `old.reddit.com/r/test/.json` → HTTP 200 (worked)
- `www.reddit.com/r/test.rss` → HTTP 200 (worked)
- `www.reddit.com/r/reddit4researchers/about.json` → HTTP 403 (blocked),
  fetched seconds later with identical headers/UA.

This pattern — a low-traffic test subreddit succeeds, a real subreddit
fails — is consistent with fingerprint/rate/reputation-based edge blocking
(Akamai/Cloudflare-style WAF) rather than a clean allow/deny split by
endpoint. It is not a reliable channel to design production infra around:
it could pass in a manual test and then fail (or vice versa) once this
project's actual server IP/UA/volume pattern is in front of it, exactly as
already observed with the 4 real stories cited in the background.

**Not independently re-confirmed with fresh 2026 primary-source text —
Pushshift.** `https://api.pushshift.io/reddit/search/submission/?q=test`
returned HTTP 403 immediately in this session's probe. That is consistent
with public knowledge that Pushshift lost its privileged Reddit API access
after Reddit's 2023 API changes and has not operated as a general-public
read API since, but I did not find or fetch a live, current (2026) Pushshist
status page or announcement to quote directly — treat the Pushshift
conclusion as "empirically still blocked, plausible per older public
reporting," not as a freshly sourced fact.

**Not independently confirmed — an authorized data-partner API.** I found
no live source (official or third-party) this session describing a
still-operating, Reddit-authorized third-party read API distinct from the
official Data API. Not ruling one out, just didn't find one.

**Confirmed but unreadable — the Public Content Policy itself.** robots.txt
names `https://support.reddithelp.com/hc/en-us/articles/26410290525844-Public-Content-Policy`
as the authoritative document governing "access and use restrictions to
Reddit content." I fetched that exact URL directly and got Cloudflare's JS
challenge page ("Just a moment... Enable JavaScript and cookies to
continue"), not the article text — an ironic, directly-observed
confirmation that Reddit's own edge tooling blocks a bare server-side
fetch even of the page that explains its access policy. I could not quote
that document's content this session.

## 4. Direct recommendation

**No clean path exists that stays narrowly read-only and skips both the
commercial-use gate and the automation ban.** Every option resolves to one
of:

- **Official Data API, done straight**: requires OAuth registration, and
  Developer Terms §4.1 requires a separate written commercial agreement
  before *any* volume of use "by or on behalf of a business or as part of
  a service or product that is monetized" — which this newspaper is. Not a
  quick, no-paperwork fix regardless of how low the call volume is.
- **`storage_state`-authenticated Playwright read (the mechanism already in
  this codebase)**: technically the closest fit to "read one known URL,
  triggered per-story, no polling" — but the User Agreement explicitly
  names exactly this ("scraping the Services... by any means (automated or
  otherwise)... without Reddit's prior written consent is prohibited"),
  drawing no exception for a real logged-in session. Building it would be
  building something Reddit's terms name and prohibit, not a gray area.
- **Bare `.json`/`.rss`/old.reddit fetch, no auth**: this is the status quo
  already failing in production; `robots.txt` is now a blanket disallow
  site-wide, and this session's own probes show it's inconsistently
  blocked (worked on a throwaway test subreddit, 403'd on a real one)
  rather than reliably open for occasional low-volume reads. Not something
  to build infra around.
- **Pushshift / third-party mirror**: empirically still blocked (403) and
  not established as currently legitimate.

**If the owner wants to close this gap anyway**, the only genuinely clean
route is the boring one implied by the Developer Terms itself: contact
Reddit and get explicit written approval for the commercial, low-volume,
read-only use case described in the background (verify one already-known
thread per triggered story, not discovery/ingest). That is a business/legal
step, not an engineering one, and it is the one path that would make the
existing `storage_state` mechanism legitimate for this use if granted —
but until that approval exists, the honest answer is: **every technical
path is either against Reddit's current terms as written, or already
observed to be unreliably blocked in practice.** There is no narrow
read-only capability this project can build today that is both clean and
would reliably resolve the 4 cited missed stories.
