import streamlit as st
import sqlite3
import requests
import json
import base64
import hashlib
import os
import re
import time
from datetime import datetime
import urllib.parse

# ==========================================
# 1. PAGE SETUP & SECURE CONFIGURATION
# ==========================================
st.set_page_config(
    page_title="Spotify Curator Search Engine",
    page_icon="🎧",
    layout="wide"
)

try:
    DEFAULT_CLIENT_ID = st.secrets["SPOTIFY_CLIENT_ID"]
except Exception:
    DEFAULT_CLIENT_ID = "4692c6625ee14e0dbe62cc59543bea43"

REDIRECT_URI = "https://open.spotify.com/"

@st.cache_resource
def get_db_connection():
    conn = sqlite3.connect("playlists.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS playlists (
        id TEXT PRIMARY KEY,
        name TEXT,
        description TEXT,
        owner_name TEXT,
        owner_url TEXT,
        saves INTEGER DEFAULT 0,
        tracks_total INTEGER DEFAULT 0,
        last_updated TEXT DEFAULT 'Unknown',
        last_updated_dt TEXT DEFAULT '1970-01-01',
        url TEXT,
        image TEXT,
        search_keyword TEXT,
        featured_artists TEXT,
        enriched INTEGER DEFAULT 0
    )
    """)
    conn.commit()
    return conn

conn = get_db_connection()
cursor = conn.cursor()

# ==========================================
# 2. SAFE NETWORK ENGINE (ANTI-BAN BACKOFF)
# ==========================================
def safe_spotify_request(url, headers, params=None, max_retries=3):
    for attempt in range(max_retries):
        try:
            res = requests.get(url, headers=headers, params=params, timeout=8)
            if res.status_code == 200:
                return res.json(), 200
            elif res.status_code == 429:
                wait_time = int(res.headers.get("Retry-After", 3)) + 1
                st.toast(f"⏳ Pacing requests for safety: waiting {wait_time}s...", icon="🛡️")
                time.sleep(wait_time)
            elif res.status_code in [401, 403, 404]:
                return None, res.status_code
        except Exception:
            time.sleep(1)
    return None, 500

# ==========================================
# 3. USER AUTHENTICATION & AUTO-REFRESH
# ==========================================
def generate_pkce_pair():
    verifier = base64.urlsafe_b64encode(os.urandom(32)).decode("utf-8").replace("=", "")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("utf-8")).digest()
    ).decode("utf-8").replace("=", "")
    return verifier, challenge

def get_valid_token():
    if st.session_state.get("auth_token"):
        return st.session_state["auth_token"]

    if os.path.exists("spotify_user_token.json"):
        try:
            with open("spotify_user_token.json", "r") as f:
                t_data = json.load(f)
            access_token = t_data.get("access_token")
            refresh_token = t_data.get("refresh_token")

            test = requests.get(
                "https://api.spotify.com/v1/me",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=4
            )
            if test.status_code == 200:
                st.session_state["auth_token"] = access_token
                return access_token

            if refresh_token:
                ref_res = requests.post(
                    "https://accounts.spotify.com/api/token",
                    data={
                        "client_id": DEFAULT_CLIENT_ID,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token
                    },
                    timeout=5
                )
                if ref_res.status_code == 200:
                    new_t = ref_res.json()
                    t_data["access_token"] = new_t["access_token"]
                    if "refresh_token" in new_t:
                        t_data["refresh_token"] = new_t["refresh_token"]
                    with open("spotify_user_token.json", "w") as f:
                        json.dump(t_data, f)
                    st.session_state["auth_token"] = new_t["access_token"]
                    return new_t["access_token"]
        except Exception:
            pass
    return None

# ==========================================
# 4. METHOD 3: EXACT SAVES & TRACK COUNT EXTRACTION
# ==========================================
def fetch_playlist_details(p_id, token):
    saves = 0
    tracks = 0
    last_updated = "Unknown"
    last_updated_dt = "1970-01-01"
    artists_set = set()

    url = f"https://api.spotify.com/v1/playlists/{p_id}"
    headers = {"Authorization": f"Bearer {token}"}
    
    data, code = safe_spotify_request(url, headers)

    if code == 200 and data:
        # Followers / Saves
        followers_obj = data.get("followers") or {}
        saves = followers_obj.get("total", 0) or 0

        # Exact Track Count from Method 3
        tracks_obj = data.get("tracks") or {}
        if isinstance(tracks_obj, dict):
            tracks = tracks_obj.get("total", 0) or len(tracks_obj.get("items", []))
            items = tracks_obj.get("items", [])
        elif isinstance(tracks_obj, list):
            tracks = len(tracks_obj)
            items = tracks_obj
        else:
            items = []

        for item in items:
            if not item:
                continue
            added = item.get("added_at")
            if added:
                try:
                    dt_str = added.split("T")[0]
                    dt = datetime.strptime(dt_str, "%Y-%m-%d")
                    if dt.strftime("%Y-%m-%d") > last_updated_dt:
                        last_updated_dt = dt.strftime("%Y-%m-%d")
                        last_updated = dt.strftime("%B %d, %Y")
                except Exception:
                    pass

            track = item.get("track")
            if track and isinstance(track, dict) and track.get("artists"):
                for a in track["artists"]:
                    if a and a.get("name"):
                        artists_set.add(a["name"].lower().strip())

    feat_str = ", ".join(list(artists_set)[:200])
    return saves, tracks, last_updated, last_updated_dt, feat_str, code

# ==========================================
# 5. DYNAMIC INTELLIGENCE ENGINES
# ==========================================
MOOD_VIBE_MAP = {
    "r&b": ["late night r&b", "chill r&b", "alt r&b", "soulful vibes"],
    "alternative": ["alt indie", "indie chill", "late night drive", "underground"],
    "indie": ["indie bedroom", "indie vibes", "feel good indie", "modern indie"],
    "pop": ["chill pop", "pop vibes", "bedroom pop", "fresh pop"],
    "hip-hop": ["chill rap", "melodic rap", "late night hip hop", "hip hop vibes"],
    "hip hop": ["chill rap", "melodic rap", "late night hip hop", "boom bap"],
    "rap": ["melodic rap", "underground rap", "chill hip hop", "hype rap"],
    "trap": ["trap beats", "dark trap", "melodic trap", "late night drive"],
    "electronic": ["chill electronic", "electronic vibes", "night drive", "club beats"],
    "house": ["deep house", "house lounge", "sunset house", "rooftop vibes"],
    "techno": ["dark techno", "warehouse rave", "minimal techno", "underground club"],
    "dance": ["workout hits", "club beats", "dance party", "weekend vibes"],
    "rock": ["modern rock", "indie rock", "alt rock", "garage rock"],
    "metal": ["heavy workout", "modern metal", "headbangers", "hard rock"],
    "punk": ["pop punk", "skate park", "angst", "garage punk"],
    "soul": ["neo soul", "chill soul", "sunday morning", "warm vibes"],
    "jazz": ["coffee shop jazz", "late night jazz", "instrumental chill"],
    "lofi": ["lofi chill", "study beats", "late night study", "cozy beats"],
    "folk": ["acoustic chill", "folk indie", "road trip", "coffeehouse"],
    "country": ["country roads", "modern country", "southern porch"],
    "latin": ["reggaeton party", "latin vibes", "perreo", "verano"],
    "phonk": ["drift phonk", "gym phonk", "night drive", "car music"],
    "shoegaze": ["dream pop", "ethereal", "staring at the ceiling", "reverb"],
    "hyperpop": ["glitchcore", "cyber", "digicore", "internet music"]
}

def analyze_specific_artist(artist_query, token):
    headers = {"Authorization": f"Bearer {token}"}
    canonical_artist = artist_query.strip().lower()
    top_tracks = []
    related_artists = set([canonical_artist])
    detected_genres = []
    detected_moods = []

    art_data, art_code = safe_spotify_request(
        "https://api.spotify.com/v1/search",
        headers,
        params={"q": artist_query, "type": "artist", "limit": 3}
    )
    if art_code == 200 and art_data:
        items = art_data.get("artists", {}).get("items", [])
        if items:
            best_match = items[0]
            for it in items:
                if it.get("name", "").strip().lower() == canonical_artist:
                    best_match = it
                    break
            canonical_artist = (best_match.get("name") or artist_query).strip().lower()
            related_artists.add(canonical_artist)
            for g in best_match.get("genres", []):
                if g.lower() not in detected_genres:
                    detected_genres.append(g.lower())

    tr_data, tr_code = safe_spotify_request(
        "https://api.spotify.com/v1/search",
        headers,
        params={"q": f'artist:"{canonical_artist}"', "type": "track", "limit": 15}
    )
    if tr_code != 200 or not tr_data or not tr_data.get("tracks", {}).get("items"):
        tr_data, tr_code = safe_spotify_request(
            "https://api.spotify.com/v1/search",
            headers,
            params={"q": canonical_artist, "type": "track", "limit": 15}
        )

    if tr_code == 200 and tr_data:
        for t in tr_data.get("tracks", {}).get("items", []):
            if not t:
                continue
            t_artists = [a.get("name", "").lower().strip() for a in t.get("artists", []) if a]
            if any(canonical_artist == a or canonical_artist in a for a in t_artists):
                for co_art in t_artists:
                    if co_art:
                        related_artists.add(co_art)
                t_name = t.get("name", "").split(" (")[0].split(" - ")[0].strip()
                if t_name and t_name not in top_tracks:
                    top_tracks.append(t_name)

    try:
        itunes_res = requests.get(
            "https://itunes.apple.com/search",
            params={"term": canonical_artist, "entity": "song", "limit": 12},
            timeout=5
        )
        if itunes_res.status_code == 200:
            for item in itunes_res.json().get("results", []):
                art_name_it = item.get("artistName", "").lower()
                if canonical_artist in art_name_it:
                    g_name = item.get("primaryGenreName", "").lower().strip()
                    if g_name and g_name not in ["music", "pop"] and g_name not in detected_genres:
                        detected_genres.insert(0, g_name)
                    elif g_name == "pop" and "pop" not in detected_genres:
                        detected_genres.append(g_name)
                    t_title = item.get("trackName", "").split(" (")[0].strip()
                    if t_title and len(top_tracks) < 6 and t_title not in top_tracks:
                        top_tracks.append(t_title)
    except Exception:
        pass

    try:
        mb_res = requests.get(
            "https://musicbrainz.org/ws/2/artist/",
            params={"query": f'artist:"{canonical_artist}"', "fmt": "json", "limit": 1},
            headers={"User-Agent": "CuratorDiscoveryApp/1.0"},
            timeout=4
        )
        if mb_res.status_code == 200:
            mb_artists = mb_res.json().get("artists", [])
            if mb_artists:
                tags = mb_artists[0].get("tags", [])
                sorted_tags = sorted(tags, key=lambda x: x.get("count", 0), reverse=True)
                for tg in sorted_tags[:4]:
                    t_val = tg.get("name", "").lower().strip()
                    if t_val and len(t_val) > 2 and t_val not in detected_genres:
                        detected_genres.append(t_val)
    except Exception:
        pass

    combined_signals = " ".join(detected_genres + [t.lower() for t in top_tracks])
    for key_word, vibe_list in MOOD_VIBE_MAP.items():
        if key_word in combined_signals:
            for v in vibe_list:
                if v not in detected_moods:
                    detected_moods.append(v)

    if not detected_moods and detected_genres:
        detected_moods = [f"{detected_genres[0]} vibes", f"chill {detected_genres[0]}"]
    elif not detected_moods:
        detected_moods = [f"{canonical_artist} vibes", f"{canonical_artist} mix"]

    educated_queries = [canonical_artist]
    for tr in top_tracks[:2]:
        educated_queries.append(f"{canonical_artist} {tr}")
        if len(tr) > 5:
            educated_queries.append(tr)

    if detected_genres:
        educated_queries.append(f"{detected_genres[0]} {canonical_artist}")
        educated_queries.append(detected_genres[0])
    if detected_moods:
        educated_queries.append(detected_moods[0])

    unique_queries = list(dict.fromkeys(educated_queries))[:6]

    profile_summary = {
        "mode": "Smart Artist/Vibe Search",
        "target": canonical_artist.title(),
        "top_tracks": top_tracks[:5],
        "genres": [g.title() for g in detected_genres[:5]] or ["Contemporary"],
        "moods": [m.title() for m in detected_moods[:4]],
        "seed_artists": list(related_artists),
        "queries_used": unique_queries
    }
    return canonical_artist, unique_queries, profile_summary


def analyze_genre_ecosystem(genre_query, token):
    headers = {"Authorization": f"Bearer {token}"}
    clean_genre = genre_query.strip().lower()
    seed_artists = set()
    seed_tracks = []
    mood_queries = []

    tr_data, tr_code = safe_spotify_request(
        "https://api.spotify.com/v1/search",
        headers,
        params={"q": f'genre:"{clean_genre}"', "type": "track", "limit": 20}
    )
    if tr_code != 200 or not tr_data or not tr_data.get("tracks", {}).get("items"):
        tr_data, tr_code = safe_spotify_request(
            "https://api.spotify.com/v1/search",
            headers,
            params={"q": clean_genre, "type": "track", "limit": 20}
        )

    if tr_code == 200 and tr_data:
        for t in tr_data.get("tracks", {}).get("items", []):
            if not t:
                continue
            t_name = t.get("name", "").split(" (")[0].strip()
            if t_name and len(seed_tracks) < 6 and t_name not in seed_tracks:
                seed_tracks.append(t_name)
            for a in t.get("artists", []):
                if a and a.get("name"):
                    seed_artists.add(a["name"].lower().strip())

    art_data, art_code = safe_spotify_request(
        "https://api.spotify.com/v1/search",
        headers,
        params={"q": f'genre:"{clean_genre}"', "type": "artist", "limit": 15}
    )
    if art_code == 200 and art_data:
        for a in art_data.get("artists", {}).get("items", []):
            if a and a.get("name"):
                seed_artists.add(a["name"].lower().strip())

    if len(seed_artists) < 8:
        try:
            mb_res = requests.get(
                "https://musicbrainz.org/ws/2/artist/",
                params={"query": f'tag:"{clean_genre}"', "fmt": "json", "limit": 15},
                headers={"User-Agent": "CuratorDiscoveryApp/1.0"},
                timeout=4
            )
            if mb_res.status_code == 200:
                for mb_a in mb_res.json().get("artists", []):
                    if mb_a.get("name"):
                        seed_artists.add(mb_a["name"].lower().strip())
        except Exception:
            pass

    for key_word, vibe_list in MOOD_VIBE_MAP.items():
        if key_word in clean_genre or clean_genre in key_word:
            mood_queries.extend(vibe_list)

    seed_artists_list = list(seed_artists)
    educated_queries = [clean_genre]
    for s_art in seed_artists_list[:3]:
        educated_queries.append(s_art)
    if seed_tracks and seed_artists_list:
        educated_queries.append(f"{seed_artists_list[0]} {seed_tracks[0]}")
    if mood_queries:
        educated_queries.append(mood_queries[0])

    unique_queries = list(dict.fromkeys(educated_queries))[:6]

    profile_summary = {
        "mode": "Genre Search (Track-Level)",
        "target": clean_genre.title(),
        "top_tracks": seed_tracks[:5],
        "genres": [clean_genre.title()],
        "moods": [m.title() for m in mood_queries[:4]] or ["Genre Curated Mix"],
        "seed_artists": seed_artists_list[:30],
        "queries_used": unique_queries
    }
    return clean_genre, unique_queries, profile_summary

# ==========================================
# 6. HARVEST & METHOD 3 ENRICHMENT
# ==========================================
def harvest_and_enrich(query, pages=2, auto_fetch_saves=True, search_mode="Keyword Search"):
    token = get_valid_token()
    if not token:
        st.error("Please connect your Spotify account in the sidebar first.")
        return

    clean_term = query.strip()
    status = st.empty()
    progress = st.progress(0)

    newly_found_ids = []

    if search_mode == "Smart Artist/Vibe Search":
        status.text(f"🧠 Profiling artist '{clean_term}'...")
        target_key, search_queries, profile_info = analyze_specific_artist(clean_term, token)
        st.session_state["active_profile"] = profile_info
        auto_fetch_saves = True
    elif search_mode == "Genre Search":
        status.text(f"🎸 Mapping '{clean_term}' genre ecosystem...")
        target_key, search_queries, profile_info = analyze_genre_ecosystem(clean_term, token)
        st.session_state["active_profile"] = profile_info
        auto_fetch_saves = True
    else:
        target_key = clean_term.lower()
        search_queries = [clean_term]
        st.session_state["active_profile"] = None

    total_search_steps = len(search_queries) * pages
    current_step = 0
    session_tag = f"[{search_mode}:{clean_term.lower()}]"

    for sq in search_queries:
        for p in range(pages):
            current_step += 1
            progress.progress((current_step / max(total_search_steps, 1)) * 0.5)
            status.text(f"🔍 Step 1/2: Scanning Spotify via query '{sq}' (Page {p+1}/{pages})...")

            url = "https://api.spotify.com/v1/search"
            headers = {"Authorization": f"Bearer {token}"}
            params = {"q": sq, "type": "playlist", "limit": 10, "offset": p * 10}

            data, code = safe_spotify_request(url, headers, params=params)
            if code != 200 or not data:
                break

            playlists_items = data.get("playlists", {}).get("items", [])
            for item in playlists_items:
                if not item or not item.get("id"):
                    continue

                p_id = item["id"]
                owner = item.get("owner") or {}
                owner_name = owner.get("display_name") or owner.get("id") or "Curator"

                if owner_name.strip().lower() == "spotify":
                    continue

                name = item.get("name") or "Untitled"
                desc = item.get("description") or ""
                owner_url = (owner.get("external_urls") or {}).get("spotify", "#")
                url_link = (item.get("external_urls") or {}).get("spotify", "#")
                images = item.get("images") or []
                img_url = images[0]["url"] if images else ""
                tr_total = (item.get("tracks") or {}).get("total", 0)

                cursor.execute("SELECT enriched, search_keyword FROM playlists WHERE id = ?", (p_id,))
                existing = cursor.fetchone()

                if existing is None:
                    cursor.execute("""
                        INSERT INTO playlists 
                        (id, name, description, owner_name, owner_url, saves, tracks_total, url, image, search_keyword, featured_artists, enriched)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 0)
                    """, (p_id, name, desc, owner_name, owner_url, 0, tr_total, url_link, img_url, session_tag))
                    if p_id not in newly_found_ids:
                        newly_found_ids.append(p_id)
                else:
                    old_kw = existing[1] or ""
                    if session_tag not in old_kw:
                        new_kw = f"{old_kw} {session_tag}".strip()
                        cursor.execute("UPDATE playlists SET search_keyword = ? WHERE id = ?", (new_kw, p_id))
                    if existing[0] == 0 and p_id not in newly_found_ids:
                        newly_found_ids.append(p_id)

            conn.commit()
            time.sleep(0.2)

    if auto_fetch_saves and newly_found_ids:
        total_to_enrich = len(newly_found_ids)
        for idx, p_id in enumerate(newly_found_ids):
            prog_val = 0.5 + (0.5 * ((idx + 1) / total_to_enrich))
            progress.progress(min(prog_val, 1.0))
            status.text(f"💾 Step 2/2: Fetching Saves, Tracks & verifying via Method 3 ({idx+1}/{total_to_enrich})...")

            s_cnt, t_cnt, l_up, l_up_dt, arts, status_code = fetch_playlist_details(p_id, token)
            if status_code == 200:
                cursor.execute("""
                    UPDATE playlists
                    SET saves = ?, tracks_total = ?,
                        last_updated = ?, last_updated_dt = ?, featured_artists = ?, enriched = 1
                    WHERE id = ?
                """, (s_cnt, t_cnt, l_up, l_up_dt, arts, p_id))
                conn.commit()
            time.sleep(0.25)

    status.empty()
    progress.empty()

# ==========================================
# 7. SIDEBAR CONTROLS
# ==========================================
st.sidebar.header("⚙️ Curator Controls")

active_token = get_valid_token()

if not active_token:
    st.sidebar.warning("🔒 Not Connected to Spotify")
    if "pkce_verifier" not in st.session_state:
        v, c = generate_pkce_pair()
        st.session_state["pkce_verifier"] = v
        st.session_state["pkce_challenge"] = c

    auth_link = (
        "https://accounts.spotify.com/authorize?"
        + urllib.parse.urlencode({
            "client_id": DEFAULT_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "code_challenge_method": "S256",
            "code_challenge": st.session_state["pkce_challenge"],
            "scope": "playlist-read-private playlist-read-collaborative"
        })
    )
    st.sidebar.markdown(f"[👉 **1. Click here to Authorize Spotify**]({auth_link})")
    redirect_input = st.sidebar.text_input("2. Paste the redirected URL here:")
    if redirect_input:
        code_match = re.search(r"code=([^&]+)", redirect_input)
        if code_match:
            auth_code = code_match.group(1)
            t_res = requests.post(
                "https://accounts.spotify.com/api/token",
                data={
                    "client_id": DEFAULT_CLIENT_ID,
                    "grant_type": "authorization_code",
                    "code": auth_code,
                    "redirect_uri": REDIRECT_URI,
                    "code_verifier": st.session_state["pkce_verifier"]
                }
            )
            if t_res.status_code == 200:
                t_json = t_res.json()
                st.session_state["auth_token"] = t_json.get("access_token")
                with open("spotify_user_token.json", "w") as f:
                    json.dump({"access_token": t_json.get("access_token"), "refresh_token": t_json.get("refresh_token")}, f)
                st.sidebar.success("✅ Connected!")
                st.rerun()
            else:
                st.sidebar.error("Code expired. Please click Authorize again.")
else:
    st.sidebar.success("🟢 Spotify Account Connected")
    if st.sidebar.button("Disconnect Account"):
        st.session_state["auth_token"] = None
        if os.path.exists("spotify_user_token.json"):
            os.remove("spotify_user_token.json")
        st.rerun()

st.sidebar.divider()

cursor.execute("SELECT COUNT(*) FROM playlists")
total_cached = cursor.fetchone()[0]
st.sidebar.metric("📦 Cached Playlists in DB", f"{total_cached:,}")

if st.sidebar.button("🗑️ Clear Local Cache"):
    cursor.execute("DELETE FROM playlists")
    conn.commit()
    st.session_state["last_query"] = None
    st.session_state["active_profile"] = None
    st.rerun()

st.sidebar.divider()
st.sidebar.subheader("🎯 Filter Results")

col_min, col_max = st.sidebar.columns(2)
with col_min:
    min_saves = st.number_input("Min Saves", min_value=0, value=0, step=10)
with col_max:
    max_saves = st.number_input("Max Saves", min_value=0, value=50000000, step=1000)

min_tracks = st.sidebar.slider("Minimum Tracks", 0, 300, 0)
pages_depth = st.sidebar.slider("Pages per Search Query (10 playlists/page)", 1, 5, 2)
auto_load_saves = st.sidebar.checkbox("Automatically fetch Saves on search", value=True)
strict_track_match = st.sidebar.checkbox("Strict Tracklist Match Only", value=False)

sort_by = st.sidebar.selectbox(
    "Sort Results By",
    ["Most Saves / Followers", "Least Saves / Followers", "Recently Updated", "Most Tracks"]
)

# ==========================================
# 8. MAIN SEARCH & RESULTS DISPLAY
# ==========================================
st.title("🎧 Spotify Playlist Curator Search Engine")

search_mode = st.radio(
    "Select Search Method:",
    ["Keyword Search", "Smart Artist/Vibe Search", "Genre Search"],
    horizontal=True
)

col_query, col_action = st.columns([4, 1])
with col_query:
    if search_mode == "Smart Artist/Vibe Search":
        placeholder_text = "Type ANY artist name (e.g., Billie Eilish, Arctic Monkeys, Drake -remix)"
    elif search_mode == "Genre Search":
        placeholder_text = "Type ANY musical genre (e.g., shoegaze, drift phonk, neo soul, bossa nova)"
    else:
        placeholder_text = "Type keywords (e.g., late night drive -metal -sleep)"

    user_search = st.text_input(
        "Search Prompt (use '-' to exclude unwanted words):",
        placeholder=placeholder_text
    )
with col_action:
    st.write("")
    st.write("")
    run_search = st.button("🚀 Search Spotify", use_container_width=True)

if user_search:
    raw_tokens = user_search.strip().split()
    include_words = []
    exclude_words = []

    for token in raw_tokens:
        clean_tok = token.strip().lower()
        if clean_tok.startswith("-") and len(clean_tok) > 1:
            exclude_words.append(clean_tok[1:])
        elif clean_tok:
            include_words.append(clean_tok)

    harvest_phrase = " ".join(include_words) if include_words else user_search.strip()
    clean_phrase = harvest_phrase.lower().strip()
    session_tag = f"[{search_mode}:{clean_phrase}]"
    cache_key = f"{search_mode}_{clean_phrase}_{pages_depth}_{auto_load_saves}"

    if st.session_state.get("last_query") != cache_key or run_search:
        harvest_and_enrich(
            harvest_phrase,
            pages=pages_depth,
            auto_fetch_saves=auto_load_saves,
            search_mode=search_mode
        )
        st.session_state["last_query"] = cache_key

    prof = st.session_state.get("active_profile")
    if search_mode in ["Smart Artist/Vibe Search", "Genre Search"] and prof:
        with st.expander(f"🧠 {prof['mode']} Profile for **{prof['target']}**", expanded=True):
            c_p1, c_p2, c_p3 = st.columns(3)
            with c_p1:
                if search_mode == "Genre Search":
                    st.markdown("**🎸 Benchmark Genre Artists Tracked:**")
                    st.write(", ".join([a.title() for a in prof["seed_artists"][:10]]) if prof["seed_artists"] else "Standard catalog")
                else:
                    st.markdown("**🔥 Artist Top Tracks Analyzed:**")
                    st.write(", ".join(prof["top_tracks"]) if prof["top_tracks"] else "Standard catalog")
            with c_p2:
                st.markdown("**🏷️ Attributed Genres:**")
                st.write(", ".join(prof["genres"]))
            with c_p3:
                st.markdown("**🌊 Target Curator Moods:**")
                st.write(", ".join(prof["moods"]))
            st.caption(f"📡 **Educated Search Queries Executed:** `{'` • `'.join(prof['queries_used'])}`")

    if sort_by == "Most Saves / Followers":
        order_by = "saves DESC"
    elif sort_by == "Least Saves / Followers":
        order_by = "saves ASC"
    elif sort_by == "Recently Updated":
        order_by = "last_updated_dt DESC"
    else:
        order_by = "tracks_total DESC"

    where_parts = [
        "saves >= ?",
        "saves <= ?",
        "tracks_total >= ?",
        "LOWER(owner_name) != 'spotify'"
    ]
    query_params = [min_saves, max_saves, min_tracks]

    if search_mode == "Smart Artist/Vibe Search":
        target_artist_canonical = (prof.get("target", clean_phrase) if prof else clean_phrase).lower()
        if strict_track_match:
            where_parts.append("LOWER(featured_artists) LIKE ?")
            query_params.append(f"%{target_artist_canonical}%")
        else:
            where_parts.append("(LOWER(featured_artists) LIKE ? OR LOWER(search_keyword) LIKE ?)")
            query_params.extend([f"%{target_artist_canonical}%", f"%{session_tag.lower()}%"])

    elif search_mode == "Genre Search":
        seed_list = prof.get("seed_artists", []) if prof else []
        if strict_track_match and seed_list:
            artist_or_clauses = ["LOWER(featured_artists) LIKE ?" for _ in seed_list[:20]]
            where_parts.append(f"({' OR '.join(artist_or_clauses)})")
            query_params.extend([f"%{a.lower()}%" for a in seed_list[:20]])
        else:
            where_parts.append("LOWER(search_keyword) LIKE ?")
            query_params.append(f"%{session_tag.lower()}%")

    else:
        for w in include_words:
            term = f"%{w}%"
            where_parts.append("""(
                LOWER(name) LIKE ? 
                OR LOWER(description) LIKE ? 
                OR LOWER(search_keyword) LIKE ?
            )""")
            query_params.extend([term, term, term])

    for w in exclude_words:
        term = f"%{w}%"
        where_parts.append("""(
            LOWER(name) NOT LIKE ? 
            AND LOWER(description) NOT LIKE ? 
            AND LOWER(featured_artists) NOT LIKE ?
        )""")
        query_params.extend([term, term, term])

    final_sql = f"""
        SELECT id, name, description, owner_name, owner_url, saves, tracks_total, last_updated, url, image, search_keyword, featured_artists, enriched
        FROM playlists
        WHERE {' AND '.join(where_parts)}
        ORDER BY {order_by}
    """

    cursor.execute(final_sql, query_params)
    results = cursor.fetchall()

    if not results:
        st.info("No playlists matched your current filters. Try unchecking 'Strict Tracklist Match Only' or lowering 'Min Saves'.")
    else:
        st.success(f"Found **{len(results)}** independent curator playlists for **{harvest_phrase.title()}**:")
        st.divider()

        seed_artists_check = [a.lower() for a in (prof.get("seed_artists", []) if prof else [])]
        target_artist_check = (prof.get("target", clean_phrase) if prof else clean_phrase).lower()

        for idx, row in enumerate(results):
            p_id, p_name, p_desc, o_name, o_url, saves, tracks, updated, p_url, img, s_kw, f_art, enriched = row
            col_cov, col_main, col_stats, col_act = st.columns([1, 3.5, 2, 1.5])

            f_art_lower = (f_art or "").lower()

            with col_cov:
                if img:
                    st.image(img, width=85)

            with col_main:
                st.markdown(f"### {idx+1}. {p_name}")
                st.markdown(f"👤 **Curator:** [{o_name}]({o_url}) • 🎵 **{tracks:,} tracks**")

                if search_mode == "Smart Artist/Vibe Search":
                    if target_artist_check in f_art_lower:
                        st.markdown(f"✅ **Confirmed Artist in Tracklist:** *{prof['target'] if prof else harvest_phrase.title()}*")
                    else:
                        matched_co = [a.title() for a in seed_artists_check if a in f_art_lower and a != target_artist_check]
                        if matched_co:
                            st.markdown(f"🌊 **Vibe & Co-Artist Match:** Features *{', '.join(matched_co[:3])}*")
                        else:
                            st.markdown(f"🌊 **Educated Mood/Genre Match** for *{ harvest_phrase.title() }*")

                elif search_mode == "Genre Search":
                    matched_genre_artists = [a.title() for a in seed_artists_check if a in f_art_lower]
                    if matched_genre_artists:
                        st.markdown(f"✅ **Confirmed {harvest_phrase.title()} Tracks Inside:** Features *{', '.join(matched_genre_artists[:4])}*")
                    else:
                        st.markdown(f"🎸 **Genre Vibe Match:** *{harvest_phrase.title()}*")

                if p_desc:
                    if len(p_desc) > 100:
                        with st.expander("📄 View Full Description"):
                            st.write(p_desc)
                    else:
                        st.caption(p_desc)

            with col_stats:
                st.metric("💾 Saves / Followers", f"{saves:,}")
                st.caption(f"📅 Last Updated: {updated}")

            with col_act:
                st.markdown(f"[🎧 Open in Spotify]({p_url})")
                if st.button("🔄 Re-check Saves", key=f"btn_{p_id}", use_container_width=True):
                    if active_token:
                        s_cnt, t_cnt, l_up, l_up_dt, arts, code = fetch_playlist_details(p_id, active_token)
                        if code == 200:
                            cursor.execute("""
                                UPDATE playlists
                                SET saves = ?, tracks_total = ?,
                                    last_updated = ?, last_updated_dt = ?, featured_artists = ?, enriched = 1
                                WHERE id = ?
                            """, (s_cnt, t_cnt, l_up, l_up_dt, arts, p_id))
                            conn.commit()
                            st.toast(f"✅ Updated! {s_cnt:,} saves & {t_cnt} tracks", icon="💾")
                            time.sleep(0.5)
                            st.rerun()
                        else:
                            st.toast(f"⚠️ Spotify returned status {code} for this playlist.", icon="❌")

            st.divider()
