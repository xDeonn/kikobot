from discord.ext import commands, tasks
import discord
from discord import app_commands
import yt_dlp as youtube_dl
from dataclasses import dataclass
import random
import asyncio
import re
from googleapiclient.discovery import build
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import os
import sys
import datetime # Added
from discord.utils import utcnow # Added

started_tasks = []

################################## SET UP ###############################################
# Make sure config.py has these or define them here
ffmpeg_path = "ffmpeg" # Default, change if you have a specific path
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")



# --- Inactivity Settings ---
INACTIVITY_TIMEOUT_MINUTES = 10 # Disconnect after 10 minutes of inactivity
# Set path to your MP3 file here, or None to disable
DISCONNECT_SOUND_PATH = "disc.mp3" # <--- CHANGE THIS to your actual MP3 file path or None

# --- Global State for Inactivity ---
last_activity_time = None

# --- Bot Setup ---
# Use commands.Bot for easier task management and context
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents) # Changed to commands.Bot
tree = bot.tree # Use the bot's tree directly

youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)

# --- Helper to update activity time ---
def update_last_activity():
    global last_activity_time
    last_activity_time = utcnow()
    # print(f"Activity detected at {last_activity_time}") # Optional: for debugging

@bot.event
async def on_ready():
    # Sync commands if needed (usually only once or after changes)
    # await tree.sync()
    # print("Commands synced.") # Optional confirmation

    welcome_messages = [
        "I'm ALIVEEEEEEEEEEEE!",
        "Ready to drop some beats!",
        "Bot online and ready for action.",
    ]
    print(random.choice(welcome_messages))
    # channel = bot.get_channel(CHANNEL_ID) # You might not need to send on ready
    # await channel.send(random.choice(welcome_messages))

    # Start the inactivity check loop
    check_inactivity.start()
    print(f"Inactivity check started. Timeout: {INACTIVITY_TIMEOUT_MINUTES} minutes.")


###############################################################################
######################## MUSIC BOT ############################################

@dataclass
class MusicQueue:
    def __init__(self):
        self.queue =[]
        self.repeat = False
        self.current_player = None # Keep track of the current player

    #Adding functions of a queue
    async def enqueue(self, url: str, requester, is_dj=False):
        # Replace youtu.be links with youtube.com/watch?v=
        if "youtu.be" in url:
            video_id = url.split("/")[-1]
            url = f"https://www.youtube.com/watch?v={video_id}"

        try:
            # Use bot.loop from commands.Bot
            player = await YTDLSource.from_url(url, loop=bot.loop, stream=True)
            if player: # Check if player was successfully created
                 # Store requester and is_dj within the player's data context if possible,
                 # or alongside it in the queue dictionary.
                 # Storing in data helps keep info tied to the source.
                player.data['requester'] = requester
                player.data['is_dj'] = is_dj
                self.queue.append({"player": player, "title": player.title, "url": player.youtube_url})
                return True # Indicate success
            else:
                print(f"Warning: Failed to create player for URL: {url}")
                return False # Indicate failure
        except Exception as e:
            print(f"Error enqueuing {url}: {e}")
            # Optionally notify the user in Discord
            # await interaction.channel.send(f"Sorry, couldn't add that song: {e}")
            return False # Indicate failure

    def dequeue(self):
        if not self.is_empty():
            return self.queue.pop(0)
        return None

    def peek(self):
        if not self.is_empty():
            return self.queue[0]
        return None

    def is_empty(self):
        return (len(self.queue) == 0)

    def clear(self):
        self.queue = []
        self.current_player = None # Clear current player too

    async def printqueue(self, interaction: discord.Interaction):
        counter = 1
        if not self.is_empty():
            embed = discord.Embed(
                title="Songs in Queue",
                description="Here are the songs currently in the queue:",
                color=discord.Color.blue()
            )

            song_list = ""
            # Add currently playing song if available
            if self.current_player and hasattr(self.current_player, 'title'):
                 song_list += f"**Now Playing:** {self.current_player.title}\n\n**Up Next:**\n"


            for item in self.queue:
                player = item["player"]
                title = item.get("title", "Unknown Title") # Get title safely
                song_entry = f"{counter}: {title}\n"
                if len(song_list) + len(song_entry) > 1020: # Embed field limit safety
                    song_list += "...\n"
                    break
                song_list += song_entry
                counter += 1

            if not song_list: # If only Now Playing was added
                song_list = "The queue is empty."
            elif counter == 1 and self.current_player: # Only Now Playing, queue empty
                 song_list += "(Queue is empty)"


            embed.add_field(name="Songs:", value=song_list or "The queue is empty.", inline=False)
            await interaction.response.send_message(embed=embed)
        elif self.current_player and hasattr(self.current_player, 'title'):
             embed = discord.Embed(
                title="Songs in Queue",
                description=f"**Now Playing:** {self.current_player.title}\n\n(Queue is empty)",
                color=discord.Color.blue()
            )
             await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message("The queue is empty and nothing is playing.")

Music_Queue = MusicQueue()

ytdl_format_options = {
    'format': 'bestaudio[ext=webm]/bestaudio[ext=mp4]/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': False, # Allow playlists explicitly if needed by URL
    'nocheckcertificate': True,
    'ignoreerrors': True, # Change to True to skip unavailable videos in playlists
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'extract_flat': True, # Faster playlist fetching if we only need URLs initially
}

ffmpeg_options_base = {
    'executable': ffmpeg_path,
    'before_options': "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    'options': '-vn' # Base options, filter added dynamically
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, youtube_url, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title', 'Unknown Title')
        # self.url = data.get('url') # This is the direct stream URL, not always needed
        self.youtube_url = youtube_url # Store the original YouTube page URL

    # Inside the YTDLSource class

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True): # Always stream=True for this bot logic
        loop = loop or asyncio.get_event_loop()
        original_url_or_query = url # Keep original for error messages/logging

        try:
            # Initial extraction: Use process=False for speed, especially with extract_flat=True for playlists/searches
            # yt-dlp handles 'ytsearch:' internally here.
            print(f"YTDL: Initial extraction for: {original_url_or_query}")
            initial_data = await loop.run_in_executor(None, lambda: ytdl.extract_info(original_url_or_query, download=False, process=False))

            if not initial_data:
                print(f"YTDL Error: No data returned for '{original_url_or_query}'.")
                return None

            video_data = None
            youtube_page_url = None

            # --- Case 1: Playlist or Search Result ---
            if 'entries' in initial_data:
                print(f"YTDL: Detected {'playlist' if initial_data.get('ie_key') == 'YoutubePlaylist' else 'search results'}.")
                # Find the first valid video entry in the list
                first_entry = next((entry for entry in initial_data.get('entries', []) if entry and entry.get('ie_key') != 'YoutubePlaylist'), None)

                if not first_entry:
                    print(f"YTDL Error: No playable video found in entries for '{original_url_or_query}'.")
                    return None

                # Get the standard youtube.com/watch?v=... URL for this specific video
                # When process=False, the entry 'url' is usually the webpage_url
                youtube_page_url = first_entry.get('url')
                entry_title = first_entry.get('title', 'Unknown Title') # Get title early for logging

                if not youtube_page_url:
                    print(f"YTDL Error: Could not get video URL from search/playlist entry '{entry_title}'.")
                    return None

                # IMPORTANT: Now, re-extract info for *this specific video URL*
                # with process=True (implied by download=False) to get stream details.
                print(f"YTDL: Processing specific video from results: {youtube_page_url} ({entry_title})")
                video_data = await loop.run_in_executor(None, lambda: ytdl.extract_info(youtube_page_url, download=False))

                if not video_data:
                     print(f"YTDL Error: Failed to process video data for specific result '{youtube_page_url}'.")
                     return None

            # --- Case 2: Direct Video URL ---
            else:
                youtube_page_url = initial_data.get('webpage_url', original_url_or_query) # Get the proper page URL
                # Check if initial data needs full processing (might be missing stream URL if process=False was somehow used)
                # 'url' key usually holds the stream url after processing
                if 'url' not in initial_data:
                    print(f"YTDL: Re-processing single video URL: {youtube_page_url}")
                    video_data = await loop.run_in_executor(None, lambda: ytdl.extract_info(youtube_page_url, download=False))
                    if not video_data:
                         print(f"YTDL Error: Failed to re-process video data for '{youtube_page_url}'.")
                         return None
                else:
                     # Already processed enough in initial call
                     video_data = initial_data

            # --- Get Stream URL and Finalize ---
            stream_url = video_data.get('url')

            # Fallback: If 'url' isn't top-level, check 'formats' for best audio
            if not stream_url:
                print(f"YTDL: Top-level 'url' not found for '{video_data.get('title', youtube_page_url)}'. Checking formats...")
                formats = video_data.get('formats', [])
                best_audio = None
                for f in reversed(formats): # Iterate from potentially higher quality down
                     # Check for audio-only formats first (often opus or m4a)
                     if f.get('acodec') != 'none' and f.get('vcodec') == 'none' and f.get('url'):
                         best_audio = f
                         print(f"YTDL: Found audio-only format: {f.get('format_id')} ({f.get('ext')})")
                         break
                # If no audio-only found, try formats with audio (might include video)
                if not best_audio:
                     for f in reversed(formats):
                         if f.get('acodec') != 'none' and f.get('url'):
                             best_audio = f
                             print(f"YTDL: Found format with audio: {f.get('format_id')} ({f.get('ext')})")
                             break

                if best_audio:
                    stream_url = best_audio.get('url')
                else:
                    print(f"YTDL Error: Could not find any playable audio stream URL for '{video_data.get('title', youtube_page_url)}'.")
                    # You could dump available formats here for debugging:
                    # print("Available formats:", formats)
                    return None # Cannot play this video

            # Prepare FFmpeg options (EQ, etc.)
            current_ffmpeg_options = ffmpeg_options_base.copy()
            # Make sure ffmpeg_path (executable) is correctly included if needed
            # If FFMPEG_PATH is just 'ffmpeg', you might not need the 'executable' key if it's in system PATH
            # If FFMPEG_PATH is a full path, 'executable' key IS needed in ffmpeg_options_base
            # Example assuming ffmpeg_path is defined globally or imported:
            current_ffmpeg_options['executable'] = ffmpeg_path # Or remove this line if relying on PATH

            eq_filters = generate_equalizer_filters(equalizer_settings)
            current_ffmpeg_options['options'] += f' -af "{eq_filters}"' if eq_filters else '' # Append audio filters

            print(f"YTDL: Creating FFmpegPCMAudio source for: {video_data.get('title', youtube_page_url)}")
            # Pass the actual STREAM URL to FFmpegPCMAudio
            return cls(discord.FFmpegPCMAudio(stream_url, **current_ffmpeg_options), data=video_data, youtube_url=youtube_page_url)

        except youtube_dl.utils.DownloadError as e:
            # Handle known yt-dlp download/extraction errors
            print(f"YTDL Download/Extraction Error for '{original_url_or_query}': {e}")
            # You might want to inform the user in Discord here
            # await interaction.followup.send(f"Error finding or processing '{original_url_or_query}': {e}", ephemeral=True)
            return None
        except Exception as e:
            # Handle other unexpected errors
            print(f"Unexpected Error in YTDLSource.from_url for '{original_url_or_query}': {type(e).__name__} - {e}")
            import traceback
            traceback.print_exc() # Print full traceback for debugging
            return None


def generate_equalizer_filters(settings):
    frequencies = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
    filters = []
    # Check if any gain is non-zero
    if any(g != 0 for g in settings.values()):
        for freq, gain in zip(frequencies, settings.values()):
            if gain != 0: # Only add filters for non-zero gains
                filters.append(f"equalizer=f={freq}:width_type=o:width=2:g={gain}")
        return ",".join(filters)
    return "" # Return empty string if all gains are zero

def format_duration(seconds):
    if seconds is None: return "N/A"
    try:
        seconds = int(seconds)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes}:{seconds:02d}"
    except (ValueError, TypeError):
        return "N/A"


def extract_playlist_id(url):
    # Improved regex to handle various YouTube playlist URL formats
    match = re.search(r"[?&]list=([^&]+)", url)
    if match:
        return match.group(1)
    return None

##########################################################################################################
#################This section is to handle the music in queue#############################################
# This task is complex and prone to race conditions.
# Let's simplify: Load a reasonable number initially (e.g., 20-50) and let users add more if needed.
# The background loading adds complexity often not needed for typical use.
# If you *really* need background loading for huge playlists, it requires careful state management.

# (Removing the enqueueremainingsongs_base, enqueue_remaining_songs, stoptask functions for simplification)
# Global variable to hold remaining songs if we decide to load in chunks (simpler approach)
# remaining_playlist_songs = []

########################################################################################################

async def update_playback(interaction: discord.Interaction):
    """Restarts the current song with updated FFmpeg options (like EQ)."""
    if interaction.guild.voice_client and (interaction.guild.voice_client.is_playing() or interaction.guild.voice_client.is_paused()):
        current_player = Music_Queue.current_player # Use the stored current player
        if current_player and hasattr(current_player, 'youtube_url'):
            # We need the original URL to re-create the source with new options
            original_url = current_player.youtube_url
            requester = current_player.data.get('requester', interaction.user) # Get original requester
            is_dj = current_player.data.get('is_dj', False)

            # Stop current playback
            interaction.guild.voice_client.stop() # This will trigger the 'after' callback if playing

            # Create new source with updated EQ
            try:
                # Defer response as this can take time
                await interaction.response.defer(thinking=True, ephemeral=True)
                new_player = await YTDLSource.from_url(original_url, loop=bot.loop, stream=True)

                if new_player:
                    # Important: Preserve requester/dj info
                    new_player.data['requester'] = requester
                    new_player.data['is_dj'] = is_dj
                    Music_Queue.current_player = new_player # Update the current player reference

                    # Play the new source
                    # The 'after' lambda needs to handle the next song correctly
                    interaction.guild.voice_client.play(new_player, after=lambda e: handle_after_play(interaction, e))

                    # Send confirmation (using followup because we deferred)
                    embed = discord.Embed(title="**Playback Updated**", description=f"Applied new settings to: {new_player.title}", color=discord.Color.blue())
                    await interaction.followup.send(embed=embed)
                    update_last_activity() # Activity occurred
                else:
                     await interaction.followup.send("Error: Could not recreate the audio stream with new settings.", ephemeral=True)

            except Exception as e:
                print(f"Error updating playback: {e}")
                await interaction.followup.send(f"An error occurred while updating playback: {e}", ephemeral=True)
        else:
             # Send response directly if not deferred
            if not interaction.response.is_done():
                await interaction.response.send_message("Could not find the current song's information to update.", ephemeral=True)
            else:
                await interaction.followup.send("Could not find the current song's information to update.", ephemeral=True)
    else:
         # Send response directly if not deferred
        if not interaction.response.is_done():
            await interaction.response.send_message("Not currently playing or paused.", ephemeral=True)
        else:
            await interaction.followup.send("Not currently playing or paused.", ephemeral=True)


async def search_youtube_playlist(genre: str):
    """Searches YouTube for playlists matching the genre."""
    search_query = f"top {genre} hits playlist official" # Added official for potentially better results
    print(f"Searching for playlists: {search_query}")
    try:
        request = youtube.search().list(
            q=search_query,
            part="snippet",
            type="playlist",
            maxResults=5 # Get a few options
        )
        response = await asyncio.to_thread(request.execute)
        playlists = []
        if response.get('items'):
            for item in response['items']:
                playlist_id = item['id']['playlistId']
                playlist_title = item['snippet']['title']
                playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
                playlists.append({"id": playlist_id, "title": playlist_title, "url": playlist_url})
            print(f"Found playlists: {[p['title'] for p in playlists]}")
            return playlists
        else:
            print("No playlists found.")
            return []
    except Exception as e:
        print(f"Error searching YouTube playlists: {e}")
        return []

async def get_playlist_songs(playlist_id: str, max_songs=50):
    """Gets song URLs and titles from a YouTube playlist ID."""
    print(f"Fetching songs for playlist ID: {playlist_id}")
    songs = []
    try:
        # Use extract_info with flat=True to quickly get video details without processing
        playlist_info = await bot.loop.run_in_executor(None,
            lambda: ytdl.extract_info(f"https://www.youtube.com/playlist?list={playlist_id}", download=False, process=False)
        )

        if playlist_info and 'entries' in playlist_info:
            count = 0
            for entry in playlist_info['entries']:
                if entry and count < max_songs:
                    # Ensure we have a URL, fallback to id if needed
                    video_url = entry.get('url') or f"https://www.youtube.com/watch?v={entry.get('id')}"
                    if video_url:
                        songs.append({
                            "url": video_url,
                            "title": entry.get('title', 'Unknown Title')
                        })
                        count += 1
                if count >= max_songs:
                    print(f"Reached max songs limit ({max_songs}) for playlist {playlist_id}")
                    break
            print(f"Fetched {len(songs)} songs for playlist {playlist_id}.")
        else:
             print(f"Could not extract entries for playlist {playlist_id}")

    except youtube_dl.utils.ExtractorError as e:
        print(f"ExtractorError fetching playlist {playlist_id}: {e}")
        # This might happen for private or unavailable playlists
    except Exception as e:
        print(f"Error getting playlist songs for {playlist_id}: {type(e).__name__} - {e}")

    return songs


# --- Playnext and After-Play Handling ---
playnext_lock = asyncio.Lock() # Prevent race conditions in playnext

def handle_after_play(interaction: discord.Interaction, error):
    update_last_activity() # Activity occurred
    """Callback function executed after a song finishes or is stopped."""
    if error:
        print(f'Error after playback: {error}')
        # Optionally send a message to the channel
        # asyncio.run_coroutine_threadsafe(interaction.channel.send(f"Playback error: {error}"), bot.loop)

    # Check if repeat is on for the specific song that just finished
    # (Need a more robust way to track if repeat was for *that* song)
    # Simple approach: If repeat is globally on, requeue the *last* played song.
    current_player = Music_Queue.current_player
    if Music_Queue.repeat and current_player:
         # Re-enqueue the song that just played *at the beginning* of the queue
         print("Repeat is ON, re-enqueuing last song.")
         # We need to recreate the player instance potentially
         # Simplest: Store URL and requester, then call enqueue again.
         original_url = current_player.youtube_url
         requester = current_player.data.get('requester', bot.user) # Default to bot if somehow missing
         is_dj = current_player.data.get('is_dj', False) # Preserve DJ status

         # Use run_coroutine_threadsafe because 'after' runs in a different thread
         async def requeue_song():
             if await Music_Queue.enqueue(original_url, requester, is_dj):
                 Music_Queue.queue.insert(0, Music_Queue.queue.pop()) # Move newly added to front
                 print(f"Re-enqueued {current_player.title} for repeat.")
         asyncio.run_coroutine_threadsafe(requeue_song(), bot.loop)


    # Schedule the next song using create_task to avoid blocking the callback thread
    bot.loop.create_task(playnext(interaction))


async def playnext(interaction: discord.Interaction):
    update_last_activity() # Activity occurred
    """Plays the next song in the queue."""
    async with playnext_lock: # Ensure only one instance runs at a time
        vc = interaction.guild.voice_client
        if not vc or not vc.is_connected():
            print("Playnext called but bot is not connected.")
            Music_Queue.current_player = None
            return

        if vc.is_playing() or vc.is_paused():
            # print("Playnext called but already playing/paused.") # Can be noisy
            return # Don't interrupt current playback

        if Music_Queue.is_empty():
            print("Queue is empty, nothing to play.")
            Music_Queue.current_player = None
            # Optional: Start inactivity timer here if queue becomes empty
            # update_last_activity() # Or maybe not, let the loop handle timeout
            return

        item = Music_Queue.dequeue()
        if not item or "player" not in item:
            print("Dequeued invalid item.")
            Music_Queue.current_player = None
            await playnext(interaction) # Try the next one
            return

        player = item["player"]
        Music_Queue.current_player = player # Store reference to current player

        # Retrieve requester and is_dj from player data if stored there
        requester = player.data.get('requester', interaction.user) # Default to interaction user if missing
        is_dj = player.data.get('is_dj', False)
        title = player.title
        url = player.youtube_url
        duration_seconds = player.data.get('duration')
        thumbnail = player.data.get('thumbnail')

        print(f"Attempting to play: {title}")

        try:
            vc.play(player, after=lambda e: handle_after_play(interaction, e))
            update_last_activity() # Music started playing

            # Send Now Playing embed
            embed = discord.Embed(title="**Now Playing**", color=discord.Color.blue())
            embed.add_field(name="Title", value=title, inline=False)
            duration_formatted = f"`{format_duration(duration_seconds)}`" if duration_seconds else "`N/A`"
            embed.add_field(name="Duration", value=duration_formatted, inline=True) # Use True for better spacing
            embed.add_field(name="Requested by", value=requester.mention, inline=True) # Show who requested

            # Shorten URL if too long for embed field
            display_url = url if len(url) <= 100 else url[:97] + "..."
            embed.add_field(name="URL", value=f"[Link]({url})", inline=False) # Use markdown link

            if thumbnail:
                embed.set_thumbnail(url=thumbnail)

            await interaction.channel.send(embed=embed) # Send to the channel where command was used

        except discord.ClientException as e:
            print(f"ClientException during playback: {e}")
            await interaction.channel.send(f"Playback error: {e}. Trying next song.")
            Music_Queue.current_player = None
            await playnext(interaction) # Try next song
        except Exception as e:
            print(f"Generic error during playback: {type(e).__name__} - {e}")
            await interaction.channel.send(f"An unexpected error occurred during playback. Trying next song.")
            Music_Queue.current_player = None
            await playnext(interaction) # Try next song


# --- Command Implementations ---

@tree.command(name="play", description="Plays a song or playlist, or adds it to the queue.")
@app_commands.describe(query="A song title, YouTube URL, or playlist URL")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer() # Acknowledge command immediately
    update_last_activity() # Command used

    # Check if user is in a VC
    if interaction.user.voice is None:
        await interaction.followup.send("You need to be in a voice channel to play music.")
        return

    # Check if bot needs to connect
    vc = interaction.guild.voice_client
    if vc is None:
        channel = interaction.user.voice.channel
        try:
            vc = await channel.connect()
            update_last_activity() # Bot joined channel
        except Exception as e:
            await interaction.followup.send(f"Failed to connect to voice channel: {e}")
            return
    elif vc.channel != interaction.user.voice.channel:
         # Optional: Move to user's channel if they are in a different one
         # await vc.move_to(interaction.user.voice.channel)
         # update_last_activity()
         # Or just tell the user:
         await interaction.followup.send("I'm already in another voice channel.")
         return


    is_playlist = "list=" in query or "/playlist/" in query

    if is_playlist:
        playlist_id = extract_playlist_id(query)
        if not playlist_id:
             # Fallback: treat as search if playlist ID extraction fails
             is_playlist = False
             print(f"Could not extract playlist ID from {query}, treating as search.")
             # Let the non-playlist logic handle it below
        else:
            await interaction.followup.send(f"🔎 Found playlist. Loading songs (this might take a moment)...")
            # Decide if we want to clear queue for a new playlist
            # Music_Queue.clear()
            # if vc.is_playing(): vc.stop()

            songs = await get_playlist_songs(playlist_id, max_songs=50) # Limit initial load
            if not songs:
                await interaction.followup.send("Couldn't find any songs in that playlist, or it might be private.")
                return

            enqueued_count = 0
            for song in songs:
                if await Music_Queue.enqueue(song['url'], interaction.user, is_dj=False):
                    enqueued_count += 1

            await interaction.followup.send(f"Added {enqueued_count} songs from the playlist to the queue.")

            # Start playback if not already playing
            if not vc.is_playing() and not vc.is_paused():
                await playnext(interaction)
            return # Finished handling playlist

    # --- Handle single song URL or search query ---
    if not is_playlist: # Explicitly check it's not a playlist handled above
        await interaction.followup.send(f"🔎 Searching for `{query}`...")
        success = await Music_Queue.enqueue(query, interaction.user, is_dj=False)

        if success:
            if not vc.is_playing() and not vc.is_paused():
                 # If nothing is playing, start immediately
                 await interaction.followup.send(f"Added to queue. Starting playback...") # Send message before playnext
                 await playnext(interaction)
            else:
                 # If already playing, just confirm it's added
                 queued_item = Music_Queue.queue[-1] # Get the item just added
                 await interaction.followup.send(f"Added **{queued_item.get('title', 'song')}** to the queue.")
        else:
             await interaction.followup.send(f"Sorry, I couldn't find or play `{query}`. Please check the link or search terms.")


@tree.command(name="repeat", description="Toggles repeat mode for the current song")
async def repeat(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    update_last_activity()
    vc = interaction.guild.voice_client
    if not vc or not vc.is_connected():
        await interaction.followup.send("I'm not in a voice channel.", ephemeral=True)
        return

    Music_Queue.repeat = not Music_Queue.repeat

    if Music_Queue.repeat:
        await interaction.followup.send("Repeat mode is now **ON** 🔁. The current song will replay after finishing.")
        # No need to immediately enqueue here, the 'after' callback handles it.
    else:
        await interaction.followup.send("Repeat mode is now **OFF** 🚫.")
        # If repeat is turned off, we might need to remove the potential duplicate
        # This logic is tricky. Simplest is to just let the current song finish.


@tree.command(name="skip", description="Skips the current song")
async def skip(interaction: discord.Interaction):
    await interaction.response.defer()
    update_last_activity()
    vc = interaction.guild.voice_client
    if not vc or not vc.is_connected():
        await interaction.followup.send("I'm not in a voice channel.", ephemeral=True)
        return
    if not vc.is_playing() and not vc.is_paused() and len(started_tasks) == 0:
        await interaction.followup.send("Nothing is playing to skip.", ephemeral=True)
        return

    skipped_title = Music_Queue.current_player.title if Music_Queue.current_player else "the current song"

    # Cancel all randomplay loops
    for task in started_tasks:
        task.cancel()
    started_tasks.clear()

    Music_Queue.repeat = False
    vc.stop()
    await interaction.followup.send(f"Skipped **{skipped_title}**.")


@tree.command(name="kill", description="Stops playback, clears queue, and leaves the channel.")
async def kill(interaction: discord.Interaction):
    await interaction.response.defer()
    update_last_activity()
    vc = interaction.guild.voice_client
    if not vc or not vc.is_connected():
        await interaction.followup.send("I'm not in a voice channel to leave.", ephemeral=True)
        return

    await interaction.followup.send("Stopping music, clearing queue, and leaving channel...")
    Music_Queue.clear()
    Music_Queue.repeat = False # Ensure repeat is off
    if vc.is_playing() or vc.is_paused():
        vc.stop() # Stop playback

    await vc.disconnect()
    global last_activity_time # Reset inactivity timer on leave
    last_activity_time = None
    print("Bot disconnected via /kill command.")


@tree.command(name="queue", description="Shows the current song queue.")
async def queue(interaction: discord.Interaction):
    # No defer needed usually, it's fast
    update_last_activity()
    vc = interaction.guild.voice_client
    # Allow showing queue even if not in VC, but maybe indicate bot state?
    # if interaction.user.voice is None:
    #     await interaction.response.send_message("You're not in a voice channel.", ephemeral=True)
    #     return

    await Music_Queue.printqueue(interaction)


@tree.command(name="reorderq", description="Moves a song in the queue. Format: <from_pos> <to_pos>")
@app_commands.describe(from_pos="Current position number of the song to move", to_pos="New position number for the song")
async def reorderq(interaction: discord.Interaction, from_pos: int, to_pos: int):
    await interaction.response.defer(ephemeral=True)
    update_last_activity()
    vc = interaction.guild.voice_client
    if not vc or not vc.is_connected():
        await interaction.followup.send("I'm not in a voice channel.", ephemeral=True)
        return

    q_len = len(Music_Queue.queue)
    if q_len == 0:
         await interaction.followup.send("The queue is empty.", ephemeral=True)
         return

    # Adjust positions to be 0-indexed and validate
    from_idx = from_pos - 1
    to_idx = to_pos - 1

    if not (0 <= from_idx < q_len and 0 <= to_idx < q_len):
        await interaction.followup.send(f"Invalid positions. Queue positions are 1 to {q_len}.", ephemeral=True)
        return

    if from_idx == to_idx:
        await interaction.followup.send("Positions are the same, no change needed.", ephemeral=True)
        return

    # Move the item
    moved_item = Music_Queue.queue.pop(from_idx)
    Music_Queue.queue.insert(to_idx, moved_item)

    await interaction.followup.send(f"Moved song from position {from_pos} to {to_pos}.")
    # Optionally show the updated queue (publicly)
    await Music_Queue.printqueue(interaction) # This will send a new public message


@tree.command(name="pause", description="Pauses the current song.")
async def pause(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    update_last_activity() # Pausing is an activity
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.followup.send("Playback paused ⏸️")
    elif vc and vc.is_paused():
         await interaction.followup.send("Playback is already paused.", ephemeral=True)
    else:
        await interaction.followup.send("Nothing is playing to pause.", ephemeral=True)


@tree.command(name="resume", description="Resumes the paused song.")
async def resume(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    update_last_activity() # Resuming is an activity
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.followup.send("Playback resumed ▶️")
    elif vc and vc.is_playing():
        await interaction.followup.send("Playback is already playing.", ephemeral=True)
    else:
        await interaction.followup.send("Nothing is paused to resume.", ephemeral=True)


@tree.command(name="clearq", description="Clears all songs from the queue.")
async def clearq(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    update_last_activity()
    vc = interaction.guild.voice_client
    # Allow clearing queue even if bot isn't connected? Maybe.
    # if not vc or not vc.is_connected():
    #      await interaction.followup.send("I'm not in a voice channel.", ephemeral=True)
    #      return

    if Music_Queue.is_empty():
         await interaction.followup.send("The queue is already empty.", ephemeral=True)
    else:
        Music_Queue.clear()
        await interaction.followup.send("Queue cleared.")

@tree.command(name="randomplay", description="Play a YouTube link or playlist at random intervals.")
async def randomplay(interaction: discord.Interaction, link: str, min_time: int, max_time: int):
    if interaction.user.voice is None:
        await interaction.response.send_message("You need to be in a voice channel first.")
        return

    if min_time > max_time or min_time < 0:
        await interaction.response.send_message("Invalid min/max values. Make sure 0 ≤ min_time ≤ max_time.")
        return

    vc = interaction.guild.voice_client
    if vc is None:
        channel = interaction.user.voice.channel
        vc = await channel.connect()
    elif vc.channel != interaction.user.voice.channel:
        await interaction.response.send_message("You're not in the same VC as the bot.")
        return

    await interaction.response.send_message(
        content=f"Started looping from: `<{link}>` every {min_time}-{max_time}s.\nUse `/skip` or `/kill` to stop.",
        allowed_mentions=discord.AllowedMentions.none(),
        suppress_embeds=True
    )

    # Check if it's a playlist
    is_playlist = "list=" in link

    playlist_songs = []

    if is_playlist:
        try:
            print(f"Randomplay: Extracting playlist from {link}")
            playlist_info = await bot.loop.run_in_executor(
                None, lambda: ytdl.extract_info(link, download=False, process=False)
            )
            if playlist_info and 'entries' in playlist_info:
                for entry in playlist_info['entries']:
                    if entry and entry.get('url'):
                        video_url = entry.get('url')
                        if "http" not in video_url:
                            video_url = f"https://www.youtube.com/watch?v={entry.get('id')}"
                        playlist_songs.append(video_url)
                print(f"Found {len(playlist_songs)} videos in playlist.")
        except Exception as e:
            print(f"Failed to extract playlist: {e}")
            await interaction.channel.send("Failed to extract playlist. Defaulting to single link mode.")
            is_playlist = False

    async def play_random_loop():
        try:
            while True:
                if interaction.guild.voice_client is None or not interaction.guild.voice_client.is_connected():
                    break

                chosen_url = random.choice(playlist_songs) if is_playlist and playlist_songs else link
                player = await YTDLSource.from_url(chosen_url, loop=bot.loop, stream=True)
                interaction.guild.voice_client.play(player)

                while interaction.guild.voice_client.is_playing() or interaction.guild.voice_client.is_paused():
                    await asyncio.sleep(1)

                sleep_time = random.randint(min_time, max_time)
                await asyncio.sleep(sleep_time)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"Error in randomplay loop: {e}")

    task = asyncio.create_task(play_random_loop())
    started_tasks.append(task)


@tree.command(name="dj", description="Loads a playlist based on a genre/artist and starts playing.")
@app_commands.describe(genre="The genre, artist, or theme for the playlist search")
async def dj(interaction: discord.Interaction, genre: str):
    await interaction.response.defer()
    update_last_activity()

    if interaction.user.voice is None:
        await interaction.followup.send("You need to be in a voice channel for DJ mode.")
        return

    vc = interaction.guild.voice_client
    if vc is None:
        channel = interaction.user.voice.channel
        try:
            vc = await channel.connect()
            update_last_activity()
        except Exception as e:
            await interaction.followup.send(f"Failed to connect to voice channel: {e}")
            return
    elif vc.channel != interaction.user.voice.channel:
         await interaction.followup.send("I'm already in another voice channel.")
         return

    await interaction.followup.send(f"🎧 DJ mode activated! Searching for '{genre}' playlists...")

    # Optional: Clear queue and stop current playback for DJ mode
    Music_Queue.clear()
    if vc.is_playing() or vc.is_paused():
        vc.stop()

    playlists = await search_youtube_playlist(genre)
    if not playlists:
        await interaction.followup.send(f"Couldn't find any suitable playlists for '{genre}'. Try a different search!")
        return

    # Pick a random playlist from the search results
    selected_playlist = random.choice(playlists)
    await interaction.followup.send(f"Found playlist: **{selected_playlist['title']}**. Loading songs...")
    print(f"DJ mode using playlist: {selected_playlist['url']}")

    songs = await get_playlist_songs(selected_playlist['id'], max_songs=50) # Load a good chunk
    if not songs:
        await interaction.followup.send("Failed to load songs from the selected playlist.")
        return

    # Shuffle the loaded songs before enqueuing
    random.shuffle(songs)

    enqueued_count = 0
    for song in songs:
        # Enqueue as DJ (bot user)
        if await Music_Queue.enqueue(song['url'], bot.user, is_dj=True):
             enqueued_count += 1

    if enqueued_count == 0:
         await interaction.followup.send("Couldn't load any playable songs from the playlist.")
         return

    await interaction.followup.send(f"Loaded {enqueued_count} songs. Let's get this party started! 🎉")
    await playnext(interaction) # Start playback


######################## EQUALIZER ############################################

equalizer_settings = {
    '32Hz': 0, '64Hz': 0, '125Hz': 0, '250Hz': 0, '500Hz': 0,
    '1kHz': 0, '2kHz': 0, '4kHz': 0, '8kHz': 0, '16kHz': 0
}

def generate_equalizer_graph(settings):
    frequencies = list(settings.keys())
    values = list(settings.values())

    fig, ax = plt.subplots(figsize=(10, 4)) # Use fig, ax for more control
    ax.plot(frequencies, values, marker='o', linestyle='-', color='skyblue')
    ax.set_title('Equalizer Settings')
    ax.set_xlabel('Frequency Band')
    ax.set_ylabel('Gain (dB)')
    ax.set_ylim(-11, 11) # Give a little padding
    ax.grid(True, linestyle='--', alpha=0.6) # Add grid
    plt.xticks(rotation=45, ha='right') # Rotate labels for better readability
    plt.tight_layout() # Adjust layout

    # Save to a file-like object in memory
    img_bytes = io.BytesIO()
    plt.savefig(img_bytes, format='png')
    plt.close(fig) # Close the figure to free memory
    img_bytes.seek(0) # Reset stream position
    return img_bytes


@tree.command(name="eq", description="Displays the current equalizer settings.")
async def eq(interaction: discord.Interaction):
    await interaction.response.defer()
    update_last_activity()
    img_bytes = generate_equalizer_graph(equalizer_settings)
    await interaction.followup.send(file=discord.File(img_bytes, filename='equalizer.png'))


@tree.command(name="eqset", description="Sets gain (-10 to 10) for a frequency band.")
@app_commands.describe(frequency="Frequency band (e.g., 125Hz, 2kHz)", value="Gain value (-10 to 10)")
@app_commands.choices(frequency=[ # Add choices for user convenience
    app_commands.Choice(name="32Hz", value="32Hz"),
    app_commands.Choice(name="64Hz", value="64Hz"),
    app_commands.Choice(name="125Hz", value="125Hz"),
    app_commands.Choice(name="250Hz", value="250Hz"),
    app_commands.Choice(name="500Hz", value="500Hz"),
    app_commands.Choice(name="1kHz", value="1kHz"),
    app_commands.Choice(name="2kHz", value="2kHz"),
    app_commands.Choice(name="4kHz", value="4kHz"),
    app_commands.Choice(name="8kHz", value="8kHz"),
    app_commands.Choice(name="16kHz", value="16kHz"),
])
async def eqset(interaction: discord.Interaction, frequency: app_commands.Choice[str], value: int):
    # Deferring here because update_playback might take time
    # await interaction.response.defer(ephemeral=True) # Defer ephemerally first
    update_last_activity()

    freq_key = frequency.value # Get the string value from the choice

    if not (-10 <= value <= 10):
        await interaction.response.send_message("Invalid value. Gain must be between -10 and 10.", ephemeral=True)
        return

    equalizer_settings[freq_key] = value
    # Send confirmation immediately before potentially slow graph/update
    await interaction.response.send_message(f"Set **{freq_key}** gain to **{value}**.", ephemeral=True)

    # Update graph and playback (use followup)
    img_bytes = generate_equalizer_graph(equalizer_settings)
    await interaction.followup.send(file=discord.File(img_bytes, filename='equalizer.png')) # Send graph publicly

    vc = interaction.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        await update_playback(interaction) # This function now handles its own responses/deferrals


# --- EQ Up/Down Commands (Example for one, repeat for others) ---
async def _adjust_eq(interaction: discord.Interaction, freq_key: str, delta: int):
    """Helper for eq up/down commands."""
    # await interaction.response.defer(ephemeral=True) # Defer ephemerally
    update_last_activity()

    current_value = equalizer_settings.get(freq_key, 0)
    new_value = current_value + delta

    if not (-10 <= new_value <= 10):
        limit = "maximum (10)" if delta > 0 else "minimum (-10)"
        await interaction.response.send_message(f"Cannot adjust **{freq_key}**. Value is already at the {limit}.", ephemeral=True)
        return

    equalizer_settings[freq_key] = new_value
    await interaction.response.send_message(f"Adjusted **{freq_key}** gain to **{new_value}**.", ephemeral=True)

    img_bytes = generate_equalizer_graph(equalizer_settings)
    await interaction.followup.send(file=discord.File(img_bytes, filename='equalizer.png'))

    vc = interaction.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        await update_playback(interaction)

@tree.command(name="equp", description="Increases gain by 1 for a frequency band.")
@app_commands.describe(frequency="Frequency band to increase")
@app_commands.choices(frequency=[app_commands.Choice(name=f, value=f) for f in equalizer_settings.keys()])
async def equp(interaction: discord.Interaction, frequency: app_commands.Choice[str]):
    await _adjust_eq(interaction, frequency.value, 1)

@tree.command(name="eqdown", description="Decreases gain by 1 for a frequency band.")
@app_commands.describe(frequency="Frequency band to decrease")
@app_commands.choices(frequency=[app_commands.Choice(name=f, value=f) for f in equalizer_settings.keys()])
async def eqdown(interaction: discord.Interaction, frequency: app_commands.Choice[str]):
    await _adjust_eq(interaction, frequency.value, -1)


@tree.command(name="eqreset", description="Resets all equalizer bands to 0.")
async def eqreset(interaction: discord.Interaction):
    # await interaction.response.defer(ephemeral=True) # Defer ephemerally
    update_last_activity()

    global equalizer_settings
    is_changed = any(v != 0 for v in equalizer_settings.values()) # Check if reset actually changes anything

    equalizer_settings = {key: 0 for key in equalizer_settings} # Reset all to 0

    await interaction.response.send_message("Equalizer settings reset to default (0).", ephemeral=True)

    img_bytes = generate_equalizer_graph(equalizer_settings)
    await interaction.followup.send(file=discord.File(img_bytes, filename='equalizer.png'))

    vc = interaction.guild.voice_client
    if is_changed and vc and (vc.is_playing() or vc.is_paused()):
        await update_playback(interaction)


# --- Audio Presets ---
audio_presets = {
    "flat": {k: 0 for k in equalizer_settings}, # Explicit flat/reset preset
    "pop": {'32Hz': -1, '64Hz': 2, '125Hz': 4, '250Hz': 5, '500Hz': 3, '1kHz': 0, '2kHz': -2, '4kHz': -3, '8kHz': -4, '16kHz': -4},
    "rock": {'32Hz': 4, '64Hz': 3, '125Hz': -2, '250Hz': -4, '500Hz': -1, '1kHz': 3, '2kHz': 5, '4kHz': 6, '8kHz': 6, '16kHz': 5},
    "jazz": {'32Hz': 3, '64Hz': 2, '125Hz': 1, '250Hz': 3, '500Hz': -2, '1kHz': -1, '2kHz': 2, '4kHz': 4, '8kHz': 4, '16kHz': 5},
    "classical": {'32Hz': 0, '64Hz': 0, '125Hz': 0, '250Hz': 0, '500Hz': 0, '1kHz': 0, '2kHz': -2, '4kHz': -4, '8kHz': -5, '16kHz': -6}, # Often subtle adjustments
    "bass_boost": {'32Hz': 7, '64Hz': 6, '125Hz': 5, '250Hz': 3, '500Hz': 1, '1kHz': 0, '2kHz': -1, '4kHz': -2, '8kHz': -2, '16kHz': -3},
    "treble_boost": {'32Hz': -3, '64Hz': -2, '125Hz': -2, '250Hz': -1, '500Hz': 0, '1kHz': 1, '2kHz': 3, '4kHz': 5, '8kHz': 6, '16kHz': 7},
    "vocal_boost": {'32Hz': -2, '64Hz': -2, '125Hz': -1, '250Hz': 0, '500Hz': 2, '1kHz': 4, '2kHz': 4, '4kHz': 2, '8kHz': 0, '16kHz': -1},
}

@tree.command(name="preset", description="Applies a pre-defined equalizer setting.")
@app_commands.describe(preset_name="The name of the preset to apply")
@app_commands.choices(preset_name=[ # Generate choices from keys
    app_commands.Choice(name=name.replace("_", " ").title(), value=name) for name in audio_presets.keys()
])
async def preset(interaction: discord.Interaction, preset_name: app_commands.Choice[str]):
    # await interaction.response.defer(ephemeral=True) # Defer ephemerally
    update_last_activity()

    preset_key = preset_name.value
    global equalizer_settings
    equalizer_settings = audio_presets[preset_key].copy() # Apply the preset

    await interaction.response.send_message(f"Applied the **{preset_name.name}** equalizer preset.", ephemeral=True)

    img_bytes = generate_equalizer_graph(equalizer_settings)
    await interaction.followup.send(file=discord.File(img_bytes, filename='equalizer.png'))

    vc = interaction.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        await update_playback(interaction)


@tree.command(name="lobotomize", description="Restarts the bot.")
async def lobotomize(interaction: discord.Interaction):
    """Restarts the bot process."""
    await interaction.response.send_message("Restarting... Aouaouaou...", ephemeral=True)
    print("Restart command received. Restarting bot...")
    # Cleanly disconnect from voice channels before restarting
    for vc in bot.voice_clients:
        try:
            Music_Queue.clear() # Clear queue for that guild
            await vc.disconnect(force=True)
            print(f"Disconnected from VC in guild {vc.guild.id} before restart.")
        except Exception as e:
            print(f"Error disconnecting from VC during restart: {e}")

    # Use os.execv to replace the current process with a new instance
    python = sys.executable
    os.execv(python, [python] + sys.argv)


# --- Inactivity Task ---
@tasks.loop(minutes=1.0) # Check every minute
async def check_inactivity():
    global last_activity_time
    # print(f"Running inactivity check... Last activity: {last_activity_time}") # Debugging

    if not bot.voice_clients:
        # Not connected anywhere, reset timer if needed
        if last_activity_time is not None:
            # print("Bot not connected to any VC, resetting inactivity timer.")
            last_activity_time = None
        return # No need to check further

    now = utcnow()
    timeout_delta = datetime.timedelta(minutes=INACTIVITY_TIMEOUT_MINUTES)

    for vc in bot.voice_clients:
        if vc.is_playing():
            # If playing, update activity time and continue to next VC
            update_last_activity() # Continuously update while playing
            # print(f"Bot is playing in {vc.guild.name}. Resetting timer.")
            continue

        # If connected but not playing (and not paused maybe?)
        if last_activity_time is None:
            # If timer isn't set, set it now (e.g., bot joined but hasn't played/had commands)
            # print(f"Bot connected in {vc.guild.name} but no activity recorded yet. Starting timer.")
            update_last_activity()
            continue # Check again next minute

        # Calculate time since last activity
        time_since_last_activity = now - last_activity_time
        # print(f"Guild {vc.guild.name}: Time since last activity: {time_since_last_activity}") # Debugging

        if time_since_last_activity > timeout_delta:
            print(f"Inactivity timeout reached in guild {vc.guild.name}. Disconnecting.")
            guild = vc.guild
            channel = vc.channel # Store channel before potential disconnect
            message_channel = guild.system_channel or next((ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages), None)

            try:
                # --- Play Disconnect Sound (Optional) ---
                sound_played = False
                if DISCONNECT_SOUND_PATH and os.path.exists(DISCONNECT_SOUND_PATH):
                    if not vc.is_playing(): # Don't interrupt if somehow playing again
                        try:
                            # Play the sound, use a future to wait for completion
                            sound_finished = asyncio.Future()
                            sound_source = discord.FFmpegPCMAudio(DISCONNECT_SOUND_PATH) # No EQ needed

                            def after_sound(error):
                                if error:
                                    print(f"Error playing disconnect sound: {error}")
                                sound_finished.set_result(True) # Signal completion regardless of error

                            vc.play(sound_source, after=after_sound)
                            print("Playing disconnect sound...")
                            await asyncio.wait_for(sound_finished, timeout=10.0) # Wait max 10s for sound
                            sound_played = True
                            print("Disconnect sound finished.")
                        except asyncio.TimeoutError:
                            print("Disconnect sound timed out.")
                            vc.stop() # Stop trying to play sound
                        except Exception as e:
                            print(f"Failed to play disconnect sound: {e}")

                # --- Disconnect ---
                await vc.disconnect()
                print(f"Successfully disconnected from {channel.name} in {guild.name} due to inactivity.")
                last_activity_time = None # Reset timer for this guild (handled globally now)

                # --- Send Message ---
                if message_channel:
                     try:
                         await message_channel.send(f"👋 Disconnected from **{channel.name}** due to inactivity ({INACTIVITY_TIMEOUT_MINUTES} minutes).")
                     except discord.Forbidden:
                         print(f"Cannot send inactivity message to {message_channel.name} (no permission).")
                     except Exception as e:
                         print(f"Error sending inactivity message: {e}")

            except Exception as e:
                print(f"Error during inactivity disconnect in {guild.name}: {e}")
                # Attempt force disconnect if failed gracefully
                try:
                    if vc.is_connected():
                        await vc.disconnect(force=True)
                except Exception as force_e:
                     print(f"Force disconnect also failed: {force_e}")
            finally:
                # Ensure timer is reset even if errors occurred during disconnect
                 last_activity_time = None

# --- Event Handler for Interaction Tracking ---
@bot.event
async def on_interaction(interaction: discord.Interaction):
    # Check if it's a command interaction (slash command, context menu, etc.)
    if interaction.type == discord.InteractionType.application_command:
        # Ignore interactions from the bot itself if necessary
        # if interaction.user == bot.user:
        #     return

        print(f"Command interaction detected: {interaction.data.get('name', 'Unknown Command')} by {interaction.user}")
        update_last_activity() # Update time on any command use

    # IMPORTANT: Process the interaction so the command framework handles it
    # commands.Bot handles this automatically, no need for manual process_application_commands
    # await bot.process_application_commands(interaction) # <-- No longer needed with commands.Bot


# --- Error Handling ---
@bot.event
async def on_command_error(ctx, error):
    # Basic error handling for prefix commands (if you add any)
    if isinstance(error, commands.CommandNotFound):
        pass # Ignore commands not found
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Missing required argument(s).")
    else:
        print(f'Ignoring exception in command {ctx.command}:', file=sys.stderr)
        traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)
        await ctx.send(f"An error occurred: {error}")

@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
     # Specific handling for app command errors
     if isinstance(error, app_commands.CommandNotFound):
         await interaction.response.send_message("Sorry, I don't recognize that command.", ephemeral=True)
     elif isinstance(error, app_commands.CheckFailure):
         await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
     elif isinstance(error, app_commands.CommandOnCooldown):
          await interaction.response.send_message(f"Slow down! Try again in {error.retry_after:.2f} seconds.", ephemeral=True)
     else:
        # Generic error message
        print(f"Error executing app command '{interaction.command.name if interaction.command else 'unknown'}': {error}", file=sys.stderr)
        # Log the full traceback for debugging
        # traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)

        # Send a user-friendly message
        error_message = f"Oops! Something went wrong while trying to run the command."
        if interaction.response.is_done():
            await interaction.followup.send(error_message, ephemeral=True)
        else:
            await interaction.response.send_message(error_message, ephemeral=True)


# --- Run the Bot ---
if __name__ == "__main__":
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN is not set in config.py or environment variables.")
    elif not YOUTUBE_API_KEY:
        print("Error: YOUTUBE_API_KEY is not set in config.py or environment variables.")
    elif not ffmpeg_path:
         print(f"Error: FFMPEG_PATH ('{ffmpeg_path}') is not valid or FFmpeg is not found.")
         print("Please ensure FFmpeg is installed and the path in config.py is correct.")
    else:
        # Ensure disconnect sound path is checked (optional)
        if DISCONNECT_SOUND_PATH and not os.path.exists(DISCONNECT_SOUND_PATH):
            print(f"Warning: Disconnect sound file not found at '{DISCONNECT_SOUND_PATH}'. Disconnect sound disabled.")
            DISCONNECT_SOUND_PATH = None # Disable if not found

        # Import io for graph generation
        import io
        import traceback # For detailed error logging

        try:
            bot.run(BOT_TOKEN)
        except discord.LoginFailure:
            print("Error: Invalid Bot Token. Please check your BOT_TOKEN in config.py.")
        except Exception as e:
            print(f"An unexpected error occurred while running the bot: {e}")