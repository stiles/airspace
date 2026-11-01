# I put a radar on my wall to see what's flying over my neighborhood

## A Raspberry Pi, a monitor and a local tracker called Airspace that answers the helicopter question while it's still overhead.

I've written before about [FlightTracer](https://medium.com/data-science-collective/what-the-heck-is-that-helicopter-doing-use-flighttracer-to-find-out-b964588e0467), a tool for asking, "What the heck was that helicopter doing?" It works well. But it's built for the past tense. By the time I've found a tail number and pulled a track, the helicopter is usually somewhere over Inglewood.

I wanted the present tense. What's flying near my house right now, who operates it and what is it doing?

So, with the help of an AI model, I built Airspace, a local aircraft tracker. Then I put it on a Raspberry Pi connected to an old monitor. Now there's a green radar screen in my house that shows every aircraft within five miles. Helicopters are amber.

[Image: the monitor showing the Airspace dashboard]

This is the screen on a Saturday afternoon. Five aircraft were within five miles, including a Cessna 172, two Embraer regional jets and N233LA, an LAPD AS350 helicopter that the dashboard flags as flying low for four minutes at about 200 feet.

That last one is the reason the project exists.

## What Airspace does

Airspace polls the [adsb.fi](https://adsb.fi) open data API for aircraft around a home point, records every response in SQLite and draws the results on a Mapbox map styled like an old radar scope. Aircraft rotate with their ground track and leave fading trails. Labels show altitude in hundreds of feet. Rings mark each mile.

It runs on one machine, makes one request at a time and serves its dashboard only to that machine. The browser never talks to the data provider.

Getting started looks like this:

```
git clone https://github.com/stiles/airspace.git && cd airspace
uv sync --extra enrich
uv run airspace init
cd web && npm install && npm run build && cd ..
uv run airspace serve
```

`init` asks for your home coordinates and a page title. Mine says "Hu-Stiles Airspace!"

## Why not just use Flightradar24?

I use Flightradar24 all the time. It's great at commercial traffic, and I have an API account for [pyfr24](https://medium.com/@stiles/from-flight-number-to-rich-data-using-pyfr24-for-aviation-analysis-10f8124f80f1), my Python package for pulling its data.

But airliners aren't what makes me walk outside. In my part of Los Angeles, it's the helicopters: police orbiting a block, news choppers hovering over a freeway chase, county fire heading somewhere in a hurry. Those are the aircraft the big tracking sites treat as just another dot, and sometimes they're filtered out entirely.

Airspace is built around them. It answers three questions.

### Is it a helicopter?

First it checks the ADS-B emitter category, which marks rotorcraft as `A7`. If that's missing, it looks up the aircraft in the FAA registry through [hangarbay](https://github.com/stiles/hangarbay), another tool of mine that keeps a local copy of the registry. If neither source says, the aircraft is unknown. Airspace doesn't guess from how it flies.

### Who's flying it?

A set of CSV catalogs maps aircraft to operators and roles: police, fire, medical, news, utility and so on. The Los Angeles file started with 52 public-agency aircraft from the FAA registry and covers LAPD, the sheriff's department, LA County Fire, the CHP and others. Every row needs a source, and rows without a confirmation date show on the dashboard as inferred.

Aircraft registered to individuals show as private, with no name. I'm curious about my neighborhood, not about my neighbors.

### What's it doing?

This is the fun part. Airspace looks at each track and flags three behaviors: circling, hovering and low flight. They show up as chips in the aircraft list and dashed rings on the map, with text like "Circling 12 min · 25 turns · radius 142 m."

These describe the track, not the reason for it. A circling police helicopter could be looking for a suspect, watching a crowd or waiting for a landing slot. The map can't tell you which.

## Triangles are not circles

The first version polled every 10 seconds, which is plenty for an airliner. It's not plenty for an LAPD helicopter flying a tight orbit at 70 knots. Between polls, maneuvering helicopters turned a median of 54 degrees, so their orbits came out as triangles and hexagons.

The data wasn't the problem. Positions were arriving less than half a second old. The polling interval was.

So Airspace now speeds up. When a helicopter or a cataloged police, fire, medical or news aircraft enters the visible area, it polls every two seconds and holds that pace for a minute after the last one leaves. The rest of the time, it goes back to every 10 seconds. adsb.fi allows one request per second, and this stays well under it.

Now circles look like circles.

## What one night looks like

Before moving it to the Pi, I ran Airspace on my laptop overnight for 13.5 hours.

It made 4,819 requests, and all of them succeeded. It saw 604 aircraft, including 19 rotorcraft. The catalog identified nine of the helicopters: five from LAPD, two from the sheriff's department, one from the CHP and one from LA County Fire. One LAPD helicopter passed within a fifth of a mile of my house.

The behavior detection found LAPD orbits lasting 30 to 54 minutes, at radii of roughly 140 to 250 meters. It also caught two helicopters hovering together at 1,800 feet for five minutes, which is what news coverage of something on the ground tends to look like.

Every poll goes into the database, so you can replay any part of a night on a timeline. Green ticks are successful polls, red ticks are failures and an amber strip marks when a helicopter was around. You can drag to any moment and bookmark it.

## Moving it to a Raspberry Pi

A laptop is a bad radar. It goes to sleep, and the lid closes. Airspace records sleep as a gap, which is honest but not very useful. I wanted something on all the time.

A Raspberry Pi 5 with 8 GB of memory turned out to be plenty. On my laptop, the collector used about 150 MB of memory and wrote about 3 MB of data an hour. It keeps a week of recordings by default, which comes to roughly 600 MB.

The setup went roughly like this:

1. Flash Raspberry Pi OS with Desktop onto a microSD card using Raspberry Pi Imager, with SSH turned on and the Wi-Fi details filled in.
2. SSH in from the Mac. The Pi never needed its own keyboard or mouse.
3. Install uv, Node.js 22 and Chromium, clone the repo and build the dashboard.
4. Run `airspace serve` and point Chromium at it in kiosk mode on the monitor.

It wasn't quite that tidy. My Pi was still set up for a Wi-Fi network I no longer use, so it stayed unreachable until I reflashed the card. And Chromium's first move on the big screen was to ask me to create a password for a new keyring, on a computer whose whole job is to show a map.

The fix is one flag:

```
chromium --password-store=basic --no-first-run --kiosk http://127.0.0.1:8000
```

The API listens only on the Pi itself. When I want to look at the radar from my laptop, an SSH tunnel brings it over without opening anything to the rest of the network:

```
ssh -N -L 8000:127.0.0.1:8000 canterlot@raspberrypi.local
```

The full setup, including the snags, is in the repo's [Raspberry Pi guide](https://github.com/stiles/airspace/blob/main/docs/pi-setup.md).

## An empty map is not an empty sky

A radar screen on the wall looks authoritative. It's worth remembering what it isn't.

adsb.fi gets its data from volunteers running receivers. Coverage depends on where those receivers are, and low-flying aircraft are the hardest to hear. Some aircraft don't broadcast at all. A helicopter can be overhead and missing from the map.

Airspace tries not to hide that. Positions age on screen between updates, fade when they get old and disappear when they're stale. If the feed goes down, the header turns red and the map goes gray, so an outage never looks like a quiet afternoon.

The "low" flag needs a caveat, too. It uses barometric altitude, which is height above sea level, not above the ground. That works on the flat parts of the basin and falls apart in the hills.

One more thing: adsb.fi's open data is for personal, noncommercial use. That's fine for a radar in my house. It's not fine for a story, so Airspace's roadmap includes a licensed provider before any of this goes near a newsroom.

## Build your own

Airspace is on [GitHub](https://github.com/stiles/airspace). You'll need Python 3.12, Node.js and a free Mapbox token. hangarbay is optional, but it makes the helicopter classification much better.

The Los Angeles catalog is the only one so far. If you know the aircraft that fly over your city, a catalog for it is a CSV file away, and pull requests are welcome.

And if you've ever stood in your driveway staring at a helicopter, trying to work out whether to go back inside: now there's a screen for that.
