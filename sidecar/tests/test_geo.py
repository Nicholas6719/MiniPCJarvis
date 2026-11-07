"""Where things are: the town table, distances from home, and ambiguity.

The case that made this necessary: on 2026-11-06 a sword incident outside a
school in Gloucester, ENGLAND reached his phone four times as "a hazard loose
in his state", because a list of town names cannot tell the two Gloucesters
apart. And the scanner's channel names are typed by hand ("Framnghm FD",
"Action PD"), so the desk needs a forgiving match.

Offline: reads the committed CSV only. Run: python tests/test_geo.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    import geo

    print("\n-- the table --")
    ts = geo.towns()
    check("all 351 Massachusetts cities and towns", len(ts) == 351, len(ts))
    check("home is Framingham", geo.distance_mi(*geo.home()) < 0.01)
    d = {n: geo.town(n).miles for n in ("Framingham", "Natick", "Sudbury", "Boston", "Waltham", "Leominster", "Pittsfield")}
    check("Natick is next door", 3 <= d["Natick"] <= 6, d["Natick"])
    check("Sudbury about six miles", 4 <= d["Sudbury"] <= 8, d["Sudbury"])
    check("Waltham about eleven", 9 <= d["Waltham"] <= 14, d["Waltham"])
    check("Boston about twenty", 18 <= d["Boston"] <= 23, d["Boston"])
    check("Leominster beyond twenty", d["Leominster"] > 20, d["Leominster"])
    check("Pittsfield nowhere near", d["Pittsfield"] > 80, d["Pittsfield"])
    within = {t.name for t in geo.towns_within(20)}
    check("his towns are within twenty miles",
          {"Framingham", "Sudbury", "Acton", "Newton", "Wellesley", "Maynard", "Natick",
           "Marlborough", "Hudson", "Waltham"} <= within, sorted(within)[:12])

    print("\n-- aliases and villages --")
    check("Marlboro is Marlborough", geo.town("Marlboro").name == "Marlborough")
    check("Saxonville is Framingham", geo.town("saxonville").name == "Framingham")
    check("Dorchester is Boston", geo.town("Dorchester").name == "Boston")
    check("Cochituate is Wayland", geo.town("Cochituate").name == "Wayland")

    print("\n-- scanner channel labels, as he typed them --")
    for label, want in (("Framingham PD   ", "Framingham"), ("Framnghm FD", "Framingham"),
                        ("Action PD", "Acton"), ("Bolylston PD", "Boylston"),
                        ("Leominister FD", "Leominster"), ("Holdon PD", "Holden"),
                        ("Southboro FD", "Southborough"), ("Newton FD 2", "Newton"),
                        ("Waltham PD 2", "Waltham"), ("Clinton Fire", "Clinton")):
        t = geo.match_town(label)
        check(f"{label.strip()!r} -> {want}", t is not None and t.name == want, t)
    check("a label with no town is not forced onto one", geo.match_town("Fireground 3") is None)

    print("\n-- the two Gloucesters --")
    uk = geo.find_towns("Two Gloucester schools in lockdown after boy hurt in incident. "
                        "Armed police are on the scene in Tuffley - pupils are safe.", "BBC")
    check("England's Gloucester is not his state", not any(f.certain for f in uk), uk)
    ma = geo.find_towns("Gloucester, Mass. harbor fire draws crews from three towns", "WCVB Boston")
    check("Gloucester, Mass. is", any(f.certain and f.town.name == "Gloucester" for f in ma), ma)
    check("a shared name with nothing placing it is not certain",
          not any(f.certain for f in geo.find_towns("Concord grapes are in season", "")))
    check("a Massachusetts outlet places a shared name",
          any(f.certain for f in geo.find_towns("Crash closes Route 20 in Sudbury", "MetroWest Daily News")))
    check("...unless the story is plainly elsewhere",
          not any(f.certain for f in geo.find_towns("Sudbury, Ontario police investigate", "Boston Herald")))
    check("a name only Massachusetts has is certain on its own",
          any(f.certain and f.town.name == "Framingham" for f in geo.find_towns("Framingham man arrested", "")))
    check("lower-case words are not towns", geo.find_towns("we went reading by the hull") == [])
    print("\n-- a town name in front of a road word is a road --")
    st = geo.find_towns("Firefighters evacuated a six-unit residential building at 211 Boylston "
                        "Street after finding the rear walls were unstable.", "WCVB Boston")
    check("211 Boylston Street is not Boylston", not any(f.town.name == "Boylston" for f in st), st)
    fr = geo.find_towns("Crews battle house fire on Concord Street in Framingham", "WCVB Boston")
    check("Concord Street in Framingham is Framingham, and only Framingham",
          [f.town.name for f in fr] == ["Framingham"], fr)
    check("Worcester Road is Route 9, not Worcester",
          not any(f.town.name == "Worcester" for f in geo.find_towns("Crash on Worcester Rd. in Natick", "")))
    check("...while Lexington Green is still Lexington",
          any(f.town.name == "Lexington" for f in geo.find_towns("Crowds gather on Lexington Green", "WCVB")))
    n = geo.nearest("Western Mass. wildfire reaches 70% containment near Holyoke", "MassLive")
    check("Western Mass. resolves to a town far away", n is not None and n.miles > 60, n)
    check("...which is outside his radius", not geo.within_radius(n.miles if n else None))

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
