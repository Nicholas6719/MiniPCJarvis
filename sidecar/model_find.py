"""Find a model somebody already sculpted, rather than inventing a bad one.

HIS REQUIREMENT: "If I say 'render Iron Man Mark III', is it going to be able to
do it? It needs to happen."

AND THE HONEST ANSWER IS THAT NOTHING ON THIS MACHINE CAN INVENT IT. Single-image
reconstruction (tier 3/4) produces a soft lump; OpenSCAD (tier 1) is a solid
modeller and cannot sculpt armour. Those are limits of the techniques, not
settings to tune.

But nobody 3D-prints an Iron Man suit by generating one. They download a model
someone spent weeks sculpting. The Mark III exists on Printables, Thingiverse,
MyMiniFactory and Sketchfab in dozens of versions. So the honest end of "do the
research you need to do" is: FIND THE EXISTING MODEL.

FOUR RULES THIS FILE KEEPS.

IT SAYS WHERE IT CAME FROM. A downloaded model is somebody else's work. It is
reported with its title, its host and its licence when one can be read, and
JARVIS never implies it made the thing. Getting this wrong would have him show a
stranger's sculpture to a friend as his own.

HE IS ASKED BEFORE ANYTHING IS DOWNLOADED. Same conversational confirmation the
long renders use, carrying what was found and how big it is — a download is an
action, and it is his machine and his disk.

WHAT ARRIVES IS CHECKED BEFORE IT IS TRUSTED. Bounded size, an extension we
actually parse, and the mesh is loaded and measured before it is offered. An STL
is inert data — never executed — and a file that will not parse is reported, not
shown.

NOTHING IS INVENTED WHEN NOTHING IS FOUND. "I couldn't find a model of that,
sir" is a real answer. Falling back to a generated blob is how he ends up with
something worse than nothing while being told it worked.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

log = logging.getLogger("jarvis.model_find")

# A model is somebody's weeks of work; a 200 MB download is not something to
# start without saying so. Anything larger is refused rather than truncated.
MAX_MODEL_BYTES = 120 * 1024 * 1024
MIN_MODEL_BYTES = 2 * 1024

# Pages that cannot be downloaded are still worth reporting — they are where the
# good models are. Kept past the GitHub results rather than sliced off.
_KEEP_PAGES = 3

# What we can actually read. A .zip is common on these sites and is handled by
# pulling the largest mesh out of it — never by running anything in it.
MESH_EXT = (".stl", ".3mf", ".obj")

# What `meshio` can actually parse today, which is what may be downloaded. Kept
# separate from MESH_EXT — that is what a link may LOOK like; this is what we
# can read. The two were conflated and `fetch` advertised OBJ and then refused
# it.
_FETCHABLE_EXT = (".stl", ".obj")
ARCHIVE_EXT = (".zip",)

# The places people actually publish printable models. Not a whitelist for
# safety — the size cap and the parser do that — but a ranking, because a result
# from Printables is far more likely to be a real model than a blog post.
KNOWN_HOSTS = (
    "printables.com", "thingiverse.com", "myminifactory.com", "cults3d.com",
    "sketchfab.com", "thangs.com", "yeggi.com", "free3d.com", "turbosquid.com",
    "github.com", "githubusercontent.com",
)

_LICENCE = re.compile(
    r"\b(CC[ -]BY(?:[ -]NC)?(?:[ -]SA)?(?:[ -]ND)?(?:\s*\d\.\d)?"
    r"|Creative Commons[^.,;]{0,40}"
    r"|MIT License|GPL(?:v?[23])?|Public Domain|CC0"
    r"|Standard Digital File License)\b", re.I)


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").replace("www.", "")
    except Exception:
        return ""


def _rank(url: str) -> int:
    """Lower is better. A direct mesh file beats a page that mentions one."""
    u = (url or "").lower()
    direct = u.endswith(MESH_EXT + ARCHIVE_EXT)
    known = any(h in _host(u) for h in KNOWN_HOSTS)
    return (0 if direct else 2) + (0 if known else 1)


async def find(description: str, limit: int = 6) -> dict:
    """Candidate models for something he named, best first.

    Returns {"candidates": [{title, url, host, direct, licence}], "query": str}.
    An empty list is a real answer.
    """
    desc = (description or "").strip()
    if not desc:
        return {"candidates": [], "query": ""}

    from tools.builtin import web_search
    # "printable" and "stl" are what separate a model from fan art and a wiki.
    query = f"{desc} 3d model stl printable download"
    try:
        found = await web_search(query, count=10)
    except Exception:
        log.debug("model search failed for %r", desc, exc_info=True)
        return {"candidates": [], "query": query}

    seen: set[str] = set()
    out: list[dict] = []
    for r in (found.get("results") or []):
        url = (r.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        u = url.lower()
        lic = _LICENCE.search(r.get("snippet") or "")
        out.append({
            "title": (r.get("title") or "").strip()[:120],
            "url": url,
            "host": _host(url),
            "direct": u.endswith(MESH_EXT + ARCHIVE_EXT),
            "licence": lic.group(0) if lic else "",
        })
    out.sort(key=lambda c: _rank(c["url"]))

    # GITHUB IS THE ONLY HOST THAT HANDS OVER THE FILE, so if the general search
    # did not surface one, ask again where the files actually are. Measured:
    # "iron man mark 3 3d model stl printable download" returns Printables and
    # Cults3D — real models, all behind a session — while the same subject with
    # `site:github.com` returns repos whose raw files download with no account.
    # Without this, tier 5 misses on subjects the web plainly has.
    if not any("github.com" in c["host"] for c in out):
        try:
            more = await web_search(f"{desc} stl site:github.com", count=8)
        except Exception:
            more = {}
        # TO THE FRONT, not the back. Appended and then sliced to `limit`, every
        # one of these fell off the end of the list and tier 5 reported nothing
        # fetchable while four real repos sat just past the cut.
        extra: list[dict] = []
        for r in (more.get("results") or []):
            url = (r.get("url") or "").strip()
            if not url or url in seen or "github.com" not in _host(url):
                continue
            seen.add(url)
            extra.append({"title": (r.get("title") or "").strip()[:120],
                          "url": url, "host": _host(url),
                          "direct": url.lower().endswith(MESH_EXT + ARCHIVE_EXT),
                          "licence": ""})
        out = extra + out

    # TWO LISTS WITH THEIR OWN ROOM, not one list and a slice. GitHub goes first
    # because it is the only host that hands over the file — but eight GitHub
    # repos then filled the whole list, and "Printables has real ones and they
    # need an account" became unsayable, which is the honest answer for Iron Man
    # and for every other character subject GitHub does not carry.
    fetchable = [c for c in out if "github.com" in c["host"]]
    pages = [c for c in out if "github.com" not in c["host"]]
    return {"candidates": fetchable[:limit] + pages[:_KEEP_PAGES],
            "query": query}


async def direct_links(page_url: str, limit: int = 6) -> list[str]:
    """Mesh files linked from a page, in the order they appear.

    Model sites mostly put the file behind a button that needs a session, so
    this finds what it can and the caller degrades honestly when it finds
    nothing rather than pretending.
    """
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                     max_redirects=4) as c:
            resp = await c.get(page_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        if resp.status_code != 200:
            return []
        html = resp.text[:400_000]
    except Exception:
        log.debug("could not read %s", page_url, exc_info=True)
        return []

    from urllib.parse import urljoin
    links = re.findall(r'href=["\']([^"\']+)["\']', html, re.I)
    out: list[str] = []
    for href in links:
        low = href.lower().split("?")[0]
        if low.endswith(MESH_EXT + ARCHIVE_EXT):
            full = urljoin(page_url, href)
            if full not in out:
                out.append(full)
        if len(out) >= limit:
            break
    return out


async def fetch(url: str, name: str = "") -> dict:
    """Download one model file and prove it is a mesh before offering it.

    An STL is inert data and is never executed. What is checked is that it is
    the right size, that it parses, and that it has geometry in it — a 404 page
    saved with a .stl extension is a real outcome of scraping links, and it must
    be reported rather than projected as an empty stage.
    """
    import httpx

    from tools.fabrication import safe_name, work_dir

    low = url.lower().split("?")[0]
    if not low.endswith(MESH_EXT + ARCHIVE_EXT):
        return {"error": "that link isn't a model file, sir"}
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True,
                                     max_redirects=4) as c:
            resp = await c.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        if resp.status_code != 200:
            return {"error": f"that download returned HTTP {resp.status_code}"}
        blob = resp.content
    except Exception as e:
        return {"error": f"I couldn't download that: {e}"}

    if not (MIN_MODEL_BYTES <= len(blob) <= MAX_MODEL_BYTES):
        return {"error": f"that file is {len(blob) // 1024} KB, sir — "
                         "outside what I'll take"}

    d = work_dir()
    base = safe_name(name or description_from(url))
    if low.endswith(ARCHIVE_EXT):
        # The largest mesh inside, and ONLY a mesh. Nothing is executed and
        # nothing is written outside the work folder.
        import io
        import zipfile
        try:
            zf = zipfile.ZipFile(io.BytesIO(blob))
        except Exception:
            return {"error": "that archive wouldn't open, sir"}
        members = [m for m in zf.infolist()
                   if m.filename.lower().endswith(_FETCHABLE_EXT)
                   and not m.is_dir() and m.file_size <= MAX_MODEL_BYTES]
        if not members:
            return {"error": "there was no mesh in that archive, sir"}
        pick = max(members, key=lambda m: m.file_size)
        ext = "." + pick.filename.lower().rsplit(".", 1)[-1]
        blob = zf.read(pick)
        inner = pick.filename
    else:
        ext = "." + low.rsplit(".", 1)[-1]
        inner = ""

    if ext not in _FETCHABLE_EXT:
        # Honestly out of scope rather than silently mis-parsed.
        return {"error": f"I can't read {ext} files yet, sir"}

    path = d / f"{base}{ext}"
    path.write_bytes(blob)

    try:
        import asyncio

        import meshio
        # OFF THE LOOP. This has just downloaded up to 120 MB, and parsing it,
        # welding it, finding its feature edges and labelling its bodies is the
        # better part of a second on the sculptures this tier actually fetches —
        # a second in which nothing else gets answered.
        info = await asyncio.to_thread(meshio.describe, str(path))
    except Exception as e:
        try:
            path.unlink()
        except OSError:
            pass
        return {"error": f"that file isn't a mesh I can read: {e}"}
    if not info.get("triangles"):
        try:
            path.unlink()
        except OSError:
            pass
        return {"error": "that file had no geometry in it, sir"}

    w, h, dp = info["size_mm"]
    return {"stl": str(path), "name": base, "from": url, "host": _host(url),
            "inner": inner, "triangles": info["triangles"],
            "size_mm": info["size_mm"], "bytes": len(blob),
            "spoken_size": f"{round(w)} by {round(h)} by {round(dp)} millimetres"}


def description_from(url: str) -> str:
    """A filename from a URL, for when he did not name it."""
    tail = (url or "").split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    return tail.rsplit(".", 1)[0][:48] or "model"


# GITHUB IS THE ONE HOST THAT ACTUALLY HANDS OVER THE FILE.
#
# Printables, Cults3D and MyMiniFactory all have the model he wants and all put
# it behind a JavaScript app and a session — scraping their pages for a mesh
# link finds nothing, which was measured rather than assumed. GitHub serves raw
# files over plain HTTP with no account, and people publish a great many printable
# models there. So a GitHub result is followed all the way to a file, and every
# other host is offered as a page for him to open.
_GH_REPO = re.compile(r"^https?://(?:www\.)?github\.com/([^/]+)/([^/#?]+)", re.I)


async def github_meshes(url: str, limit: int = 8) -> list[dict]:
    """The STL files in a public GitHub repo, largest first.

    Largest first because in a repo of armour parts the big file is the piece
    and the small ones are test coupons and brackets.
    """
    m = _GH_REPO.match(url or "")
    if not m:
        return []
    owner, repo = m.group(1), m.group(2).removesuffix(".git")
    import httpx
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as c:
            r = await c.get(
                f"https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD"
                "?recursive=1", headers={"User-Agent": "jarvis"})
        if r.status_code != 200:
            return []
        tree = (r.json() or {}).get("tree") or []
    except Exception:
        log.debug("github tree failed for %s", url, exc_info=True)
        return []

    out, pointers = [], []
    for t in tree:
        path = t.get("path") or ""
        # OBJ TOO, now that the hologram can read one. STL carries three
        # vertices and a normal because that is all a printer needs; anything
        # sculpted is published as OBJ, and this scan was skipping all of it.
        if not path.lower().endswith(_FETCHABLE_EXT):
            continue
        size = int(t.get("size") or 0)
        entry = {
            "path": path,
            "bytes": size,
            "url": f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/"
                   + "/".join(p for p in path.split("/")),
            "repo": f"{owner}/{repo}",
            "lfs": False,
        }
        # A GIT LFS POINTER IS 130-ODD BYTES OF TEXT, and the size filter was
        # throwing away exactly the repos worth having: every mesh in
        # `Poesghost/mandalorian_helmet` is a pointer, so the one repo on GitHub
        # that actually holds Mandalorian helmet shells looked empty. The
        # pointer states the true size, and media.githubusercontent.com serves
        # the content with no account — measured, an 11.3 MB shell downloads.
        if _LFS_POINTER_BYTES[0] <= size <= _LFS_POINTER_BYTES[1]:
            entry["lfs"] = True
            entry["url"] = (f"https://media.githubusercontent.com/media/"
                            f"{owner}/{repo}/HEAD/" + path)
            pointers.append(entry)
            continue
        if not (MIN_MODEL_BYTES <= size <= MAX_MODEL_BYTES):
            continue
        out.append(entry)

    # Resolve what the pointers actually weigh, so they sort against real files
    # rather than all looking like 133 bytes. Bounded: these are tiny and few.
    if pointers:
        await _resolve_lfs(owner, repo, pointers[:_MAX_POINTER_READS])
        out += [p for p in pointers
                if MIN_MODEL_BYTES <= p["bytes"] <= MAX_MODEL_BYTES]

    out.sort(key=lambda f: -f["bytes"])
    return out[:limit]


# A pointer file is a fixed little document: a version URL, an oid and a size.
# Anything in this range that claims to be an STL is one of those rather than a
# mesh, and nothing outside it is.
_LFS_POINTER_BYTES = (100, 400)
_MAX_POINTER_READS = 14
_LFS_SIZE = re.compile(r"^size (\d+)$", re.M)


async def _resolve_lfs(owner: str, repo: str, pointers: list) -> None:
    """Read each pointer for the size of the file it stands for."""
    import asyncio

    import httpx

    async def one(c, p):
        try:
            r = await c.get(f"https://raw.githubusercontent.com/{owner}/{repo}"
                            f"/HEAD/{p['path']}",
                            headers={"User-Agent": "jarvis"})
            got = _LFS_SIZE.search(r.text or "")
            p["bytes"] = int(got.group(1)) if got else 0
        except Exception:
            p["bytes"] = 0

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            await asyncio.gather(*(one(c, p) for p in pointers))
    except Exception:
        log.debug("could not resolve LFS pointers in %s/%s", owner, repo,
                  exc_info=True)
