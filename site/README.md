# orivexs.team

The Orivexs Team website: plain HTML, CSS and JavaScript with no build step.

```
site/
├── index.html           home: hero, services, Apex Core install, IT topics, contact
├── apex-core/index.html Apex Core product page
├── about/index.html     Oliver's CV dashboard
├── 404.html
├── CNAME                orivexs.team
└── assets/              site.css, site.js, favicon.svg
```

Every page works without JavaScript. `site.js` only adds the OS install tabs (picking
the visitor's OS), copy buttons, scroll reveals and the terminal playback. Animations
are switched off for visitors who ask for reduced motion.

## Preview locally (CachyOS / any Linux)

```bash
python -m http.server 8000 -d site
# open http://localhost:8000
```

## Before going live

1. **Merge the Apex Core code into `main`.** The install commands on the site are the
   ones in the Apex Core README: `pipx install "git+https://github.com/oliverhyar12-coder/Apex-Core.git"`.
   That installs from the default branch, so it fails until the code is on `main`.
2. **Fill in the placeholders** on `about/index.html`: the two `[YEAR]` timeline rows
   and `[ADD YOUR STACK]`. Check the repair service lines match what you actually offer.
3. **Set up email for oliver@orivexs.team** (see below). Owning the domain does not
   give you a mailbox.

## Deploy with GitHub Pages

The workflow `.github/workflows/pages.yml` publishes `site/` whenever `site/` changes
on `main`, or on demand from the Actions tab.

1. Repository **Settings → Pages → Build and deployment → Source: GitHub Actions**.
2. Merge this branch into `main` (or run the "Deploy site" workflow).
3. **Settings → Pages → Custom domain:** `orivexs.team`, then Save.
4. At your domain registrar, add these DNS records for `orivexs.team`:

   | Type  | Name  | Value                          |
   |-------|-------|--------------------------------|
   | A     | `@`   | `185.199.108.153`              |
   | A     | `@`   | `185.199.109.153`              |
   | A     | `@`   | `185.199.110.153`              |
   | A     | `@`   | `185.199.111.153`              |
   | AAAA  | `@`   | `2606:50c0:8000::153`          |
   | AAAA  | `@`   | `2606:50c0:8001::153`          |
   | AAAA  | `@`   | `2606:50c0:8002::153`          |
   | AAAA  | `@`   | `2606:50c0:8003::153`          |
   | CNAME | `www` | `oliverhyar12-coder.github.io` |

   Remove any parking-page A/CNAME records the registrar added for `@` or `www`.
5. Wait for DNS (minutes to a few hours), then tick **Enforce HTTPS** in Settings → Pages.
6. Recommended: verify the domain under your GitHub **profile Settings → Pages → Add a
   domain**, which gives you a `TXT` record to add. This stops anyone else from claiming
   `orivexs.team` on GitHub Pages.

Check DNS from your terminal:

```bash
dig +short orivexs.team A
dig +short www.orivexs.team CNAME
```

## Email for oliver@orivexs.team

Pick a mail provider and add the `MX` (and `TXT`/SPF) records it gives you at the
registrar, next to the records above. Options: your registrar's own email forwarding,
Cloudflare Email Routing (free forwarding to an existing inbox, if Cloudflare runs your
DNS), Zoho Mail, Proton Mail or Google Workspace. Send a test message to
oliver@orivexs.team before you publish the site.

## Other hosts

The folder is a static site, so it also works on Cloudflare Pages or Netlify: set the
build command to nothing and the output directory to `site`. `404.html` uses root-relative
links, so serve the site at the domain root.
