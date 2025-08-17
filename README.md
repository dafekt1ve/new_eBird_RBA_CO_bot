# Colorado's Discord RBA Bot

A Discord bot for managing Rare Bird Alerts (RBA) in Colorado.  

This bot fetches data from the [eBird API](https://ebird.org/home) and provides:  

- County and statewide rare bird alerts.
- CO State Review Checklist moderation workflow.
- Thread tracking with recency badges.
- Accept/Reject moderation buttons for new checklist submissions.
- Persistence via SQLite for robust state management.

---

## Features

### User Commands
- `!getname <banding_code>` → Lookup species name by banding code.
- `!getbc <species_name>` → Lookup banding code by species name.
- `!rba <county_name|region_code>` → Fetch the latest rare bird alerts and display them in human-readable form.

### Scheduled Tasks
- Posts RBAs at 7am and 5pm to county-level channels.
- Updates statewide threads with recency badges for recent sightings.
- Tracks positive and missed checklists for the last 24 hours, 1–3 days, 3–7 days, 7–10 days, and >10 days.

### Moderation Workflow
- Checklists that meet CO State Review criteria are sent to moderators.
- Accept/Reject buttons update the moderation queue and persist to SQLite.
- Accepted checklists create or update threads in the `co-statewide-rba` channel.

### Data Persistence
- Uses SQLite for:
  - Thread tracking (`threads` table)
  - Checklist tracking (`checklists` table)
  - Moderation queue (`moderation_queue` table)
  - Missed checklists (`misses` table)

---

## Setup

1. **Clone the repository**
```bash
git clone https://github.com/username/new_eBird_RBA_CO_bot.git
cd new_eBird_RBA_CO_bot
```

2. **Install Dependencies**
```bash 
pip install -r requirements.txt
```

3. **Set Environment Variables**
- DISCORD_TOKEN → Discord bot token
- EBIRD_TOKEN → eBird API token

4. **Run The Bot**
```bash
python bot.py
```

