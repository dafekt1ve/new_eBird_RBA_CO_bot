import os
from ebird_api import fetch_ebird_rba
import discord
from discord.ext import tasks
from discord_messages import chunked_rba_messages
from db import save_checklist, get_all_threads, save_thread, get_checklists_for_thread
from time_utils import ebird_local_to_utc, get_timezone_name
from datetime import datetime, timezone, timedelta, time
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from models import Observation
from co_county_lookup import lookup_region_code
from tasks import build_region_channels_map, rba_task
from discord.ext import commands
import logging
import requests
import json

# Create a logger object
logger = logging.getLogger("Dipper_RBA_Bot")
logger.setLevel(logging.DEBUG)  # Set minimum level to capture

# Create formatter
formatter = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

# File handler
file_handler = logging.FileHandler("Dipper_RBA_Bot.log", encoding="utf-8")
file_handler.setFormatter(formatter)

# Add both handlers to the logger
logger.addHandler(console_handler)
logger.addHandler(file_handler)

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
EBIRD_TOKEN = os.getenv("EBIRD_TOKEN")

# Discord bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', case_insensitive=True, intents=intents)

GUILD_ID = int(os.getenv("GUILD_ID"))

MT = ZoneInfo("America/Denver")
region_channels = None  # global cache

@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user}")
    guild = bot.get_guild(GUILD_ID)
    
    if not guild:
        logger.error("Guild not found")
    else:
        logger.info(f"Connected to guild: {guild.name}")

    # Load previous taxonomy snapshot if it exists
    if os.path.exists("taxonomy_snapshot.json"):
        with open("taxonomy_snapshot.json", "r") as f:
            old_data = json.load(f)
    else:
        old_data = []

    url = "https://api.ebird.org/v2/ref/taxonomy/ebird?locale=en&fmt=json"
    payload={}
    headers = {
        'X-eBirdApiToken': EBIRD_TOKEN
    }
    res = requests.get(url, headers=headers, data=payload)
    data = res.json()

    def build_name_map(taxonomy_data):
        return {entry["speciesCode"]: entry["comName"] for entry in taxonomy_data}

    old_names = build_name_map(old_data)
    new_names = build_name_map(data)

    name_changes = {}
    for code, new_name in new_names.items():
        old_name = old_names.get(code)
        if old_name and old_name != new_name:
            name_changes[old_name] = new_name

    for old, new in name_changes.items():
        logger.info(f"Taxonomy update: '{old}' → '{new}'")

    # Optional: detect new species codes
    old_codes = set(old_names)
    new_codes = set(new_names)
    new_species = new_codes - old_codes
    if new_species:
        logger.warning(f"New species codes detected: {new_species}")

    with open("taxonomy_snapshot.json", "w") as f:
        json.dump(data, f, indent=2)


    bot.codesList = []
    f = open("codesList.txt", "w")
    for d in data:
        species = {'comName': d['comName'], 'bandingCodes': d['bandingCodes'], 'comNameCodes': d['comNameCodes'], 'speciesCode': d['speciesCode']}
        f.write(f'{species}\n')
        bot.codesList.append(species)
    f.close()
    
    # logger.debug(bot.codesList)

    # Start the scheduled RBA loop
    if not scheduled_rba.is_running():
        scheduled_rba.start()


async def handle_rba_command(channel, region_code: str):
    # Normalize user input
    region_name_norm = region_code.strip().lower()

    # Look up region code in DB
    region_code = lookup_region_code(region_name_norm)
    if not region_code:
        await channel.send(f"Could not find a region matching '{region_code}'.")
        return

    # Now use the region_code for eBird API
    recent_obs_dicts = fetch_ebird_rba(region_code)

    recent_obs = []
    for d in recent_obs_dicts:
        lat = d.get("lat")
        lon = d.get("lng")
        if lat is None or lon is None:
            tz_name = "UTC"
        else:
            tz_name = get_timezone_name(lat, lon)

        obs_utc = ebird_local_to_utc(d.get("obsDt"), lat, lon)

        obs = Observation(
            checklist_id=d.get("subId"),
            species=d.get("comName"),
            region=region_code,
            location=d.get("locName", "Unknown"),
            observer=d.get("userDisplayName", "Unknown"),
            obs_datetime=obs_utc,
            local_tz=tz_name,
            thread_tracker_key=None,
            lat=lat,
            lon=lon,
            has_media=bool(d.get("hasRichMedia", []))  # True if there are media items
        )
        recent_obs.append(obs)

    messages = chunked_rba_messages(recent_obs)

    for msg in messages:
        await channel.send(msg, silent=True)


async def scheduled_rba_fetch():
    for region in REGION_CODES:
        recent_obs = fetch_ebird_rba(region)
        for obs in recent_obs:
            obs.obs_datetime = ebird_local_to_utc(obs.obs_datetime, obs.lat, obs.lon)
            save_checklist(obs)
        await update_threads_for_region(region_code)
        # Optionally notify moderators or log

       
def identify_pending_moderation():
    return get_pending_moderation()  # from db.py

async def send_to_moderators(mod_list):
    for mod_item in mod_list:
        # send Discord message with accept/reject buttons
        pass

async def handle_moderation_action(checklist_id, action, moderator):
    if action == "accept":
        # create thread in Discord
        # save_thread in db
        update_moderation_status(checklist_id, "accepted", moderator)
    elif action == "reject":
        update_moderation_status(checklist_id, "rejected", moderator)

def compute_recency(thread_tracker_key: str) -> str:
    """Compute recency bucket based on the most recent checklist in UTC."""
    checklists = get_checklists_for_thread(thread_tracker_key)
    if not checklists:
        return "No reports"

    # Find the latest checklist timestamp
    latest_dt = max(obs.obs_datetime for obs in checklists)

    # Current time in UTC
    now_utc = datetime.now(timezone.utc)

    delta = now_utc - latest_dt

    if delta < timedelta(days=1):
        return "<24h"
    elif delta < timedelta(days=3):
        return "1-3d"
    elif delta < timedelta(days=7):
        return "3-7d"
    else:
        return ">7d"

async def update_threads_for_region(region_code: str, discord_client: discord.Client = None):
    """Update threads for a region with new recency buckets. Optionally edit Discord threads."""
    threads = get_all_threads()
    for thread in threads:
        # Filter by region (tracker key format: "species|region")
        _, thread_region = thread.tracker_key.split("|")
        if thread_region != region_code:
            continue

        # Compute new recency
        new_bucket = compute_recency(thread.tracker_key)
        thread.status_bucket = new_bucket
        save_thread(thread)

        # Optionally update Discord thread message if client is provided
        if discord_client and hasattr(thread, "discord_channel_id"):
            try:
                channel = discord_client.get_channel(thread.discord_channel_id)
                if channel:
                    msg = f"Recency update: {thread.tracker_key} is now in bucket {new_bucket}"
                    await channel.send(msg)
            except Exception as e:
                print(f"Failed to update Discord thread {thread.thread_id}: {e}")

@tasks.loop(time=[time(7, 0, tzinfo=MT), time(17, 0, tzinfo=MT)])
async def scheduled_rba():
    global region_channels
    if region_channels:
        await rba_task(region_channels)
    else:
        print("[RBA] No region channels mapped yet.")

@scheduled_rba.before_loop
async def before_scheduled_rba():
    global region_channels
    await bot.wait_until_ready()
    guild = bot.get_guild(int(GUILD_ID))
    region_channels = await build_region_channels_map(guild)

@bot.command()
async def rba(ctx, *arg):
    logger.info(f"Called rba in a DM @ {datetime.now()} for {arg} from {ctx.message.author.name}")
    await handle_rba_command(ctx.channel, ' '.join(arg))


@bot.command()
async def getName(ctx, *arg):
    logger.info(f"Called getName in a DM @ {datetime.now()} for {arg} from {ctx.message.author.name}")
    if len(arg) < 1:
        embed = discord.Embed(title="Example:", description="If you send me a message with the text: \n**!getName AMDI**\n\n I will reply with: \n**American Dipper**", color=0xFFD700)
        await ctx.send(embed=embed, silent=True)
        return
    bc = ' '.join(arg)
    bc = bc.upper()

    logger.info(f"'{bc}'")
 
    speciesNames = []
    speciesNames2 = []
    for i in bot.codesList:
        if bc in i['bandingCodes']:
            species = i['comName']
            logger.info(f"Found species in bandingCodes: {species}")
            speciesNames.append(species)
            
        if bc in i['comNameCodes']:
            species = i['comName']
            logger.info(f"Found species in comNameCodes: {species}")
            speciesNames2.append(species)

    logger.debug(len(speciesNames))
    logger.debug(speciesNames)
    logger.debug(speciesNames2)

    if (len(speciesNames) > 1):
        await ctx.send(f'Species name needs disambiguation.  Possible answers are: {speciesNames}')
    elif not speciesNames:
        if not speciesNames2:
            await ctx.send('No match for banding code found.')
            return
        else:
            embed = discord.Embed(title="Warning:", color=0xffff00)
            text = (f'This code needs disambiguation.  Possible species are: ')
            speciesNames.extend(speciesNames2)
            text2 = '\n'.join(speciesNames)
            text = text + '\n' + text2
            embed.description = text
            await ctx.send(embed=embed)
    else:
        await ctx.send(str(speciesNames[0]))

@bot.command()
async def getBC(ctx, *arg):
    logger.info(f"Called getBC in a DM @ {datetime.now()} for {arg} from {ctx.message.author.name}")
    if len(arg) < 1:
        embed = discord.Embed(title="Example:", description="If you send me a message with the text: \n**!getBC American Dipper** \n\nI will respond with: \n**AMDI**", color=0xFFD700)
        await ctx.send(embed=embed, silent=True)
        return
    args = ' '.join(arg)
    cn = args.lower()
    speciesCodes = []
    for i in bot.codesList:
        if cn == i['comName'].lower():
            species = i['bandingCodes']
            if not species:
                continue
            else:
                speciesCodes.append(species)
    if (len(speciesCodes) > 1):
        await ctx.send(f'Species name needs disambiguation.  Possible answers are: {speciesCodes}')
    elif not speciesCodes:
        await ctx.send('No matching species found.')
    else:
        await ctx.send(str(speciesCodes[0]).replace('[', '').replace(']','').replace("'",""))

bot.run(TOKEN)