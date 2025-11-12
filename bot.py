import os
import asyncio
import random
import time
from datetime import datetime, UTC 
from dotenv import load_dotenv
import re

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject 
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError 

from pymongo import MongoClient
from bson import ObjectId

# -------------------------
# Load env (BOT_TOKEN, MONGO_URI, BOT_USERNAME)
# -------------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

# --- CRITICAL CONFIG ---
# NOTE: This value is CRITICAL for the link button to work correctly.
BOT_USERNAME = os.getenv("BOT_USERNAME", "UoG_confessions_bot").lstrip('@').lower() 

if not BOT_TOKEN or not MONGO_URI:
    raise RuntimeError("Please set BOT_TOKEN and MONGO_URI in your .env file")

# ------------------------
# Config (CRUCIAL: CHECK THESE IDs)
# -------------------------
# Admin ID (Current: 905781541) - REPLACE WITH YOUR ADMIN ID
ADMIN_IDS = [905781541] 
# Group ID (Current: 5099572645) - REPLACE WITH YOUR GROUP ID
GROUP_ID = int(-5099572645) 
# Channel ID (Current: -1003276055222) - REPLACE WITH YOUR CHANNEL ID
CHANNEL_ID = int(-1003276055222) 

CONFESSION_COOLDOWN = 60 * 5  # 5 minutes cooldown between submissions

# Tags based on your request images, for the user to choose
AVAILABLE_TAGS = [
    "Relationship", "Love", "Crush", "Family", "Friendship", "Sexual",
    "Life", "Motivation", "Advice", "Campus", "Dorm", "Experience",
    "Weird", "Funny", "Secret", "Money", "Health", "Mental",
    "Info", "Personal", "Business", "Religion", "Trauma", "Exam",
    "School", "Other"
]

# -------------------------
# DB Setup
# -------------------------
client = MongoClient(MONGO_URI)
db = client["confessionBot"]
conf_col = db["Confessions"]
settings_col = db["Settings"] 
karma_col = db["Karma"] 
users_col = db["Users"] 

# --- Initialize Global Auto-Approve State ---
try:
    current_settings = settings_col.find_one({"_id": "auto_approve_status"})
    GLOBAL_AUTO_APPROVE = current_settings.get("enabled", False) if current_settings else False
except Exception as e:
    print(f"Error loading settings: {e}")
    GLOBAL_AUTO_APPROVE = False


# -------------------------
# Bot & Dispatcher
# -------------------------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ------------------------
# User Profile Constants
# ------------------------
DEFAULT_NICKNAME = "anonymous"
DEFAULT_EMOJI = "👤"
# Limited emoji options as requested
EMOJI_OPTIONS = [
    "🌟", "🚀", "💡", "🔮", "📚", "🎨", "🎭", "🎵", "☕", "💻", 
    "🦊", "🦁", "🦉", "🦋", "🐉", "🐙", "🌈", "🔥", "💧", "🌍"
]
MAX_NICKNAME_LENGTH = 20

# -------------------------
# FSM States
# -------------------------
class ReplyStates(StatesGroup):
    waiting_for_reply = State()

class ConfessStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_tags = State()

class CommentStates(StatesGroup):
    # State for both new comments and replies to comments
    waiting_for_submission = State() 

# NEW PROFILE STATES
class ProfileStates(StatesGroup):
    editing_nickname = State()
    editing_bio = State() # Renamed to avoid conflict with old state definition
    choosing_emoji = State()

last_confession_time = {}

# -------------------------
# Utility Functions
# -------------------------
# NOTE: The Firestore path functions are included below as placeholders, 
# but the bot uses MongoDB (Motor) as defined above for persistent storage.

def get_profile_collection_name(user_id):
    """Returns the private collection path for user profiles."""
    # Placeholder for canvas environment compatibility, actual bot uses MongoDB
    appId = 'default-app-id'
    return f"/artifacts/{appId}/users/{user_id}/profiles"

def get_likes_collection_name(appId):
    """Returns the public collection path for likes."""
    # Placeholder for canvas environment compatibility, actual bot uses MongoDB
    appId = 'default-app-id'
    return f"/artifacts/{appId}/public/data/likes"

def get_confession_collection_name(appId):
    """Returns the public collection path for confessions."""
    # Placeholder for canvas environment compatibility, actual bot uses MongoDB
    appId = 'default-app-id'
    return f"/artifacts/{appId}/public/data/confessions"

def get_comments_collection_name(appId):
    """Returns the public collection path for comments."""
    # Placeholder for canvas environment compatibility, actual bot uses MongoDB
    appId = 'default-app-id'
    return f"/artifacts/{appId}/public/data/comments"


# Helper: Text Truncation 
# -------------------------
def truncate_text(text: str, max_length: int) -> str:
    """Truncates text to max_length and adds ellipsis if cut."""
    if not text:
        return ""
    if len(text) > max_length:
        return text[:max_length - 3] + "..."
    return text

# -------------------------
# Helper: Get User Profile Data (UPDATED)
# -------------------------
# Adapted from your provided async logic to your existing synchronous MongoDB calls
def get_user_profile(user_id):
    """Retrieves or initializes user profile data."""
    profile = users_col.find_one({"_id": user_id})
    if not profile:
        profile = {
            "_id": user_id,
            "nickname": DEFAULT_NICKNAME, # NEW
            "emoji": DEFAULT_EMOJI,       # NEW
            "bio": "Default bio: Tell us about yourself!", # Bio updated to match old implementation's default text
            "aura_points": 0,             # NEW (Replacing old 'karma')
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC), # NEW
        }
        users_col.insert_one(profile)
    return profile

def format_profile_message(profile: dict, user_id: int, karma_score: int):
    """Formats the profile message, using the old 'karma' as 'Aura' for display."""
    nickname = profile.get("nickname", DEFAULT_NICKNAME)
    emoji = profile.get("emoji", DEFAULT_EMOJI)
    bio = profile.get("bio", "No bio set 🤫")
    # Using the existing karma_score from the separate 'karma' collection for now, 
    # but displaying it as 'Aura' for the user's view.
    aura = karma_score 

    return (
        f"{emoji} **{nickname}'s Profile**\n"
        f"🆔 User ID: `{user_id}`\n\n"
        f"✨ **Aura:** `{aura}` points (from post/comment voting)\n\n"
        f"📝 **Bio:**\n"
        f"_{bio}_\n\n"
        f"Use /leaderboard to see the top Aura holders!"
    )

# ------------------------
# Keyboard Builders (NEW/UPDATED)
# ------------------------

def get_profile_menu_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for the main profile view."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Edit Profile", callback_data="profile_edit")
    # Temporarily remove Leaderboard until implemented
    # builder.button(text="🏆 Leaderboard", callback_data="show_leaderboard")
    builder.adjust(1)
    return builder.as_markup()

def get_edit_profile_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for the profile editing menu."""
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐ Edit Nickname", callback_data="edit_nickname")
    builder.button(text="📝 Edit Bio", callback_data="edit_bio")
    builder.button(text="🎨 Change Emoji", callback_data="change_emoji")
    builder.button(text="⬅️ Back to Profile", callback_data="profile_view")
    builder.adjust(1)
    return builder.as_markup()

def get_emoji_picker_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for selecting a profile emoji."""
    builder = InlineKeyboardBuilder()
    for emoji in EMOJI_OPTIONS:
        builder.button(text=emoji, callback_data=f"set_emoji:{emoji}")
    builder.button(text="⬅️ Back to Edit Menu", callback_data="profile_edit")
    builder.adjust(5) # 5 emojis per row
    return builder.as_markup()


# -------------------------
# Helper: Generates a consistent anonymous ID map for comments on a post
# -------------------------
def generate_anon_id_map(comments):
    """
    Generates a consistent mapping of user_id to an anonymous number (e.g., Anon 1).
    """
    anon_map = {}
    anon_counter = 1
    
    # Iterate through comments to build the map based on the order they appear
    for comment in comments:
        user_id = comment.get('user_id')
        if user_id and user_id not in anon_map:
            # Use the user's configured nickname and emoji for their comments
            profile = get_user_profile(user_id)
            nickname = profile.get("nickname", DEFAULT_NICKNAME)
            emoji = profile.get("emoji", DEFAULT_EMOJI)
            anon_map[user_id] = f"{emoji} {nickname} {anon_counter}" # Updated Anon format
            anon_counter += 1
            
    return anon_map

# ----------------------------------------------------
# Helper: show confession and comments
# ----------------------------------------------------
# (show_confession_and_comments function is retained, but depends on the updated generate_anon_id_map)

# ... (show_confession_and_comments function remains unchanged other than its dependency)

async def show_confession_and_comments(msg: types.Message, conf_id: str):
    """
    Fetches, formats, and displays the confession post and its comments as a chain of messages.
    """
    user_id = msg.from_user.id
    
    try:
        doc = conf_col.find_one({"_id": ObjectId(conf_id), "approved": True})
    except Exception:
        doc = None
        
    if not doc:
        await msg.answer("❌ Confession not found or not approved.")
        return

    # --- 1. Format the Main Confession Text (Only post details) ---
    tags_text = ' '.join([f'#{t.replace(" ", "_")}' for t in doc.get("tags", [])])
    
    main_confession_text = doc.get('text', '') 
    
    conf_text = (
        f"**📜 Confession #{doc.get('number')}**\n\n"
        f"{main_confession_text}\n\n"
        f"`{tags_text}`\n"
        f"**Main Post Votes:** 👍 {doc.get('likes', 0)} | 👎 {doc.get('dislikes', 0)}"
    )

    # --- 2. Build the Keyboard for the Main Post (Add Comment + Post Voting) ---
    comments = doc.get("comments", [])
    full_kb_builder = InlineKeyboardBuilder()
    
    # Row 1: 'Add Comment' button (parent_index is -1 for top-level comment)
    # The parent_index here is for a TOP-LEVEL comment (parent_index: -1).
    # The parent_index passed to this callback is NOT the index of the Telegram Message.
    full_kb_builder.row(
        InlineKeyboardButton(text="✍️ + Add Comment (Anonymous)", callback_data=f"comment_start:{conf_id}:-1")
    )

    # Row 2: Main post voting buttons (if not the confessor)
    is_confessor = doc.get("user_id") == user_id
    if not is_confessor:
        full_kb_builder.row(
            InlineKeyboardButton(text=f"👍 ", callback_data=f"vote:like:{conf_id}"),
            InlineKeyboardButton(text=f"👎 ", callback_data=f"vote:dislike:{conf_id}")
        )
    
    kb = full_kb_builder.as_markup()
    
    # --- 3. Send the Main Message (The start of the chain) ---
    main_message = None
    try:
        if doc.get("media"):
            # If media is present, the text must be capped for the caption limit (1024)
            caption_text = truncate_text(conf_text, 1000)
            main_message = await msg.answer_photo(
                photo=doc["media"], 
                caption=caption_text, 
                reply_markup=kb, 
                parse_mode="Markdown"
            )
        else:
            main_message = await msg.answer(conf_text, reply_markup=kb, parse_mode="Markdown")
            
        main_message_id = main_message.message_id

    except TelegramAPIError as e:
        print(f"Error sending main confession view: {e}")
        await msg.answer("⚠️ Could not display the confession.")
        return

    # --- 4. Send Comments as Separate Reply Messages ---
    if comments:
        anon_map = generate_anon_id_map(comments) # Uses UPDATED generate_anon_id_map
        # CRITICAL CHANGE: Map to store the Telegram message ID of each sent comment
        comment_msg_id_map = {} 
        
        # Limit displayed comments to 10 for performance and chat history reasons
        for i, comment in enumerate(comments[:10]): 
            comment_number = i + 1 
            # anon_id is now the emoji + nickname + number (e.g., '🌟 anon 1')
            anon_id = anon_map.get(comment.get('user_id'), f"Anon {comment_number}") 
            c_likes = comment.get('likes', 0)
            c_dislikes = comment.get('dislikes', 0)
            
            # --- START OF CUSTOMIZATION FOR COMMENT DISPLAY ---
            
            # 1. Get reply context
            # This is the INDEX of the comment this new comment is replying to (if > -1).
            replying_to_index = comment.get('parent_index', -1) 
            # This is the ANON ID string (e.g., 'Anon 1')
            replying_to_anon_id = comment.get('replying_to_anon') 

            # 2. Determine Reply Message ID (The core fix)
            reply_to_id = None
            
            if replying_to_index != -1 and replying_to_index in comment_msg_id_map:
                # Case A: Replying to another comment. Use that comment's Message ID.
                reply_to_id = comment_msg_id_map[replying_to_index]
            
            elif replying_to_index == -1:  # FIXED: correct logic for replying to main post
                # Fallback Case: If replying to the main post (should be rare due to parent_index logic, 
                # but safe to keep as main_message_id)
                 reply_to_id = main_message_id
            
            # Note: Case B is implicitly handled by reply_to_id = None
            
            # 3. Add visual prefix (↩️ In reply to...) ONLY if it is a reply
            prefix = ""
            if replying_to_anon_id:
                # This ensures the prefix is only added when the comment is an actual reply
                prefix = f"↩️ In reply to **{replying_to_anon_id}**\n"
            
            # 4. Build the final message text 
            comment_text = comment.get('text', 'Comment text missing.') 
            comment_msg_text = (
                f"**#{comment_number}.** **{anon_id}**\n\n"
                f"{prefix}" 
                f"{comment_text}"
            )
            
            # 5. Keyboard for Comment Voting and Reply
            # IMPORTANT: The 'Reply' button must point to the CURRENT comment's index (i)
            comment_kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"👍 ({c_likes})", 
                        callback_data=f"cmt_vote:like:{conf_id}:{i}"
                    ),
                    InlineKeyboardButton(
                        text=f"👎 ({c_dislikes})", 
                        callback_data=f"cmt_vote:dislike:{conf_id}:{i}"
                    ),
                    InlineKeyboardButton(
                        text=f"↩️ Reply", 
                        # CRITICAL: Parent index for reply button is the index of *this* comment (i)
                        callback_data=f"comment_start:{conf_id}:{i}" 
                    )
                ]
            ])
            
            # --- END OF CUSTOMIZATION FOR COMMENT DISPLAY ---
            
            # Send the comment as a new message, replying to the conditional target
            sent_comment = await msg.answer(
                comment_msg_text,
                reply_to_message_id=reply_to_id, # Use conditional reply ID
                reply_markup=comment_kb,
                parse_mode="Markdown"
            )
            
            # CRITICAL: Store the message ID for use by replies to this comment
            comment_msg_id_map[i] = sent_comment.message_id

        
        if len(comments) > 10:
             await msg.answer(
                f"*... and {len(comments) - 10} more comments not shown.*",
                reply_to_message_id=main_message_id,
                parse_mode="Markdown"
            )


# -------------------------
# /start - Updated to handle deep links and FSM context
# -------------------------
@dp.message(Command("start"))
async def cmd_start(msg: types.Message, command: CommandObject, state: FSMContext): 
# ... (cmd_start remains unchanged)
    # Use command.args to safely get the payload part of the deep link
    payload = command.args
    
    if payload:
        # Deep link detected, check if it's a comment link
        payload_match = re.match(r'^comment_([0-9a-fA-F]+)$', payload)
        
        if payload_match:
            # Deep link is for viewing comments
            conf_id = payload_match.group(1)
            # FIX: Use the injected state object to clear context
            await state.clear() 
            
            # Send a new chain of messages for the post view
            await msg.answer(
                "Loading post view... Note: Voting/actions will generate a new message chain for up-to-date information."
            )
            await show_confession_and_comments(msg, conf_id)
            return

    # Default /start behavior (only runs if no valid deep link payload is found)
    await msg.answer("🤖 Confession Bot online.\nUse /confess to submit anonymously.\nUse /profile to manage your identity.\nUse /help for commands.")

# -------------------------
# /confess flow (private only) - STEP 1: Text/Media
# -------------------------
# ... (cmd_confess_start, handle_confession_text, handle_tag_selection, submit_confession_to_db remain unchanged)

@dp.message(Command("confess"))
async def cmd_confess_start(msg: types.Message, state: FSMContext):
# ... (cmd_confess_start remains unchanged)
    if msg.chat.type != "private":
        return
    
    # Check rate limit
    user_id = msg.from_user.id
    now = time.time()
    last = last_confession_time.get(user_id, 0)
    if now - last < CONFESSION_COOLDOWN:
        remain = int((CONFESSION_COOLDOWN - (now - last)) // 60) + 1
        await msg.answer(f"⏳ Please wait {remain} more minute(s) before sending another confession.")
        return

    # Clear any active comment states before starting a new confession
    await state.clear() 
    
    await msg.answer("✍️ **Step 1/2:** Send me your anonymous confession now (text or image with caption).")
    await state.set_state(ConfessStates.waiting_for_text)

@dp.message(ConfessStates.waiting_for_text)
async def handle_confession_text(msg: types.Message, state: FSMContext):
# ... (handle_confession_text remains unchanged)
    # This check ensures commands don't proceed into the FSM state
    if msg.text and msg.text.startswith("/"):
        await msg.answer("Confession cancelled. Use /confess to start again.")
        await state.clear()
        return

    text = None
    media = None
    if msg.photo:
        media = msg.photo[-1].file_id
        text = msg.caption or "(image confession)"
    elif msg.text:
        text = msg.text
    else:
        await msg.answer("Please send a valid text or image confession. Try again.")
        return

    if not text or len(text.strip()) < 10:
        await msg.answer("Your confession seems too short or empty (min 10 characters). Please try again.")
        return

    # Store confession draft and move to tag selection
    await state.update_data(confession_text=text, confession_media=media, user_id=msg.from_user.id, selected_tags=[])

    # Build tag selection keyboard
    builder = InlineKeyboardBuilder()
    for tag in AVAILABLE_TAGS:
        builder.button(text=tag, callback_data=f"tag:{tag}")
    
    builder.button(text="🧠 Auto-Categorize", callback_data="tag:Auto") 
    builder.button(text="✅ Done (Submit)", callback_data="tag:Done")
    builder.adjust(3)

    await msg.answer(
        "🏷️ **Step 2/2:** Select one or more relevant topic tags using the buttons below, or choose 'Auto-Categorize'.",
        reply_markup=builder.as_markup()
    )
    await state.set_state(ConfessStates.waiting_for_tags)

@dp.callback_query(ConfessStates.waiting_for_tags)
async def handle_tag_selection(callback: types.CallbackQuery, state: FSMContext):
# ... (handle_tag_selection remains unchanged)
    await callback.answer()
    data = await state.get_data()
    selected_tags = data.get("selected_tags", []) 
    
    if not callback.data.startswith("tag:"):
        return

    action = callback.data.split(":")[1]

    if action == "Done":
        if not selected_tags: 
            await callback.message.answer("Please select at least one tag or choose 'Auto-Categorize' before submitting.")
            return
        
        # --- Submission Logic (Final Step) ---
        await submit_confession_to_db(callback.message, state, selected_tags)
        return

    elif action == "Auto":
        # Simulate auto-categorization by selecting a default tag
        if "Other" not in selected_tags:
            selected_tags.append("Other")
        
        await state.update_data(selected_tags=selected_tags)
        
        await callback.message.answer(
            "🧠 **Auto-Categorize:** 'Other' has been added as a tag. Press 'Done' to submit or add more tags."
        )

    # Regular Tag selection/deselection
    tag = action
    
    if tag in AVAILABLE_TAGS:
        if tag in selected_tags:
            selected_tags.remove(tag)
        else:
            selected_tags.append(tag)
    
    await state.update_data(selected_tags=selected_tags)
    
    # Rebuild keyboard to show selected state
    builder = InlineKeyboardBuilder()
    for t in AVAILABLE_TAGS:
        emoji = "☑️" if t in selected_tags else ""
        builder.button(text=f"{emoji} {t}", callback_data=f"tag:{t}")
        
    builder.button(text="🧠 Auto-Categorize", callback_data="tag:Auto")
    builder.button(text="✅ Done (Submit)", callback_data="tag:Done")
    builder.adjust(3)

    tags_list = ' '.join([f'#{t}' for t in selected_tags]) if selected_tags else "None"
    
    try:
        await callback.message.edit_text(
            f"🏷️ **Step 2/2:** Select one or more relevant tags. (Selected: {tags_list})",
            reply_markup=builder.as_markup()
        )
    except Exception:
        pass 

# Submission function (handles DB insertion and approval logic)
async def submit_confession_to_db(msg: types.Message, state: FSMContext, tags: list):
# ... (submit_confession_to_db remains unchanged)
    data = await state.get_data()
    user_id = data["user_id"]
    text = data["confession_text"]
    media = data.get("confession_media")
    tags_text = ' '.join([f'#{t.replace(" ", "_")}' for t in tags]) # Format tags for display

    # Ensure profile exists before submission (initializes profile if first time)
    get_user_profile(user_id) 

    doc = {
        "text": text,
        "media": media,
        "tags": tags,
        "user_id": user_id,
        "created_at": datetime.now(UTC),
        "approved": GLOBAL_AUTO_APPROVE, # Use global setting
        "approved_at": datetime.now(UTC) if GLOBAL_AUTO_APPROVE else None, 
        "number": next_conf_number() if GLOBAL_AUTO_APPROVE else None,
        "channel_message_id": None,
        "likes": 0,
        "dislikes": 0,
        "voters": {}, # Tracks which user voted on this post to prevent duplicate voting
        "comments": [] # Initialize comments array
    }

    res = conf_col.insert_one(doc)
    doc["_id"] = res.inserted_id
    last_confession_time[user_id] = time.time()
    await state.clear()
    
    # Auto-Approval Path
    if GLOBAL_AUTO_APPROVE:
        final_doc, success = await publish_confession(doc, tags_text)
        if success:
            await msg.answer(f"✅ Submitted and **AUTO-APPROVED**! Your confession is now live as **Confession #{final_doc['number']}**.")
        else:
            await msg.answer("⚠️ Submitted, but failed to post to the channel. Admins have been notified.")
    # Manual Approval Path
    else:
        await msg.answer("✅ Submitted! Admins will review and approve if it follows the rules. You will be notified privately when it's approved or rejected.")
        
        # Send to admins for manual review
        kb = admin_kb(str(res.inserted_id))
        
        # Truncate text for admin caption if media is present (Limit 1024 characters)
        admin_text = text
        if media:
            admin_text = truncate_text(text, 1000)

        admin_message = f"📝 Pending Confession (ID: {res.inserted_id}, User: {user_id}, Tags: {tags_text})\n\n{admin_text}"
        
        for aid in ADMIN_IDS:
            try:
                if media:
                    await bot.send_photo(aid, media, caption=admin_message, reply_markup=kb)
                else:
                    await bot.send_message(aid, admin_message, reply_markup=kb)
            except Exception as e:
                print(f"ERROR sending pending confession to admin {aid}: {e}")

# -------------------------
# Helper: publish approved confession (No change)
# -------------------------
# ... (next_conf_number and publish_confession remain unchanged)
def next_conf_number():
    return int(conf_col.count_documents({"approved": True}) + 1)

async def publish_confession(doc: dict, tags_text: str):
    final_number = doc.get("number") or next_conf_number()
    
    # Truncate the confession text if media is present (Caption limit is 1024)
    main_text = doc.get('text','')
    media = doc.get("media")
    
    if media:
        # If posting with media, caption is limited to 1024. Truncate to 1000.
        main_text = truncate_text(main_text, 1000)
    
    text = f"📢 Confession #{final_number}\n\n{main_text}\n\n{tags_text}"
    conf_id = str(doc["_id"])
    
    # --- CRITICAL FIX: NEW DEEP-LINK FORMAT ---
    # When clicked, the bot receives /start comment_{conf_id}
    bot_url = f"https://t.me/{BOT_USERNAME}?start=comment_{conf_id}" 
    
    # Show current like/dislike counts (always 0 on initial post)
    likes = doc.get('likes', 0)
    dislikes = doc.get('dislikes', 0)
    
    reaction_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"👍  ({likes})", callback_data=f"vote:like:{conf_id}"),
            InlineKeyboardButton(text=f"👎  ({dislikes})", callback_data=f"vote:dislike:{conf_id}")
        ],
        [
            # UPDATED BUTTON TEXT AND LINK
            InlineKeyboardButton(text="💬 View / Add Comment", url=bot_url) 
        ]
    ])
    
    channel_msg = None
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            if media:
                channel_msg = await bot.send_photo(CHANNEL_ID, media, caption=text, reply_markup=reaction_kb)
            else:
                # FIX: Only send the message once, with the keyboard attached
                channel_msg = await bot.send_message(CHANNEL_ID, text, reply_markup=reaction_kb)
            
            # Since we can't reliably get the group message ID, we only store the channel ID for editing votes
            if channel_msg:
                update_fields = {
                    "approved": True, 
                    "approved_at": datetime.now(UTC),
                    "number": final_number,
                    "channel_message_id": channel_msg.message_id
                }
                conf_col.update_one({"_id": doc["_id"]}, {"$set": update_fields})
                doc.update(update_fields)
            
            try:
                # Also send to the group chat
                if media:
                    await bot.send_photo(GROUP_ID, media, caption=text, reply_markup=reaction_kb)
                else:
                    await bot.send_message(GROUP_ID, text, reply_markup=reaction_kb)
            except Exception as e:
                print(f"Error publishing to group: {e}")

            return doc, True 

        except (TelegramBadRequest, TelegramForbiddenError) as e:
            error_message = str(e)
            print(f"CRITICAL TELEGRAM API ERROR on channel publish (Attempt {attempt + 1}/{max_attempts}): {error_message}")
            
            if "chat not found" in error_message or "bot is not an administrator" in error_message or "Bad Request: chat not found" in error_message:
                for aid in ADMIN_IDS:
                    await bot.send_message(aid, f"🚨 **CRITICAL CONFIGURATION ERROR** 🚨\n\nConfession #{final_number} failed to post.\nReason: Bot cannot access Channel ID `{CHANNEL_ID}`. Ensure the bot is an admin with 'Post messages' permission.\nError: {error_message}")
                return doc, False 

        except Exception as e:
            error_message = str(e)
            print(f"GENERAL ERROR publishing to channel (Attempt {attempt + 1}/{max_attempts}): {error_message}")
        
        if attempt < max_attempts - 1:
            await asyncio.sleep(2 ** (attempt + 1)) 
        else:
            for aid in ADMIN_IDS:
                await bot.send_message(aid, f"⚠️ **POSTING FAILURE** ⚠️\n\nConfession #{final_number} failed to post to the channel after {max_attempts} retries.\nLast Error: {error_message}")
            return doc, False 
    
    return doc, False 

# -------------------------
# NEW/UPDATED: Notification Helper (Now accepts keyboard) (No change)
# -------------------------
# ... (send_notification remains unchanged)
async def send_notification(user_id, message_text, reply_markup=None):
    """Sends a private notification, handling potential blocks."""
    try:
        await bot.send_message(user_id, message_text, reply_markup=reply_markup, parse_mode="Markdown")
        return True
    except TelegramForbiddenError:
        print(f"User {user_id} blocked the bot.")
        return False
    except Exception as e:
        print(f"Error sending notification to {user_id}: {e}")
        return False

# -------------------------
# COMMENT/REPLY FLOW START (Callback) (No change)
# -------------------------
# ... (cb_comment_start remains unchanged)
@dp.callback_query(F.data.startswith("comment_start:"))
async def cb_comment_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    
    if callback.message.chat.type != "private":
        await callback.message.answer("Please interact with this bot in a private chat to comment or reply.")
        return

    try:
        # Data format: comment_start:{conf_id}:{parent_index}
        # parent_index is the 0-based index of the comment being replied to.
        # -1 means it's a top-level comment (replying to the main post).
        _, conf_id, parent_index_str = callback.data.split(":")
        parent_index = int(parent_index_str)
    except Exception:
        await callback.message.answer("Error parsing confession ID or index.")
        await state.clear()
        return

    doc = conf_col.find_one({"_id": ObjectId(conf_id), "approved": True})
    if not doc:
        await callback.message.answer("❌ This confession is no longer available.")
        await state.clear()
        return

    # Store the confession ID and parent index in FSM context
    await state.update_data(target_conf_id=conf_id, parent_index=parent_index)
    await state.set_state(CommentStates.waiting_for_submission)
    
    if parent_index == -1:
        await callback.message.answer(
            "💬 **Submit Your Anonymous Comment**\n"
            "Please type your comment now. It will be posted anonymously under the confession."
        )
    else:
        comments = doc.get("comments", [])
        if parent_index >= len(comments):
             await callback.message.answer("Error: Cannot find parent comment. Starting new top-level comment instead.")
             await state.update_data(parent_index=-1)
             return
             
        # We need the anon map to identify who we are replying to for the display text
        anon_map = generate_anon_id_map(comments)
        parent_user_id = comments[parent_index].get('user_id')
        parent_anon_id = anon_map.get(parent_user_id, f"Anon {parent_index + 1}")
        
        # Display the comment we are replying to
        parent_text_preview = truncate_text(comments[parent_index].get('text', '...'), 50)
        
        await callback.message.answer(
            f"↩️ **Replying to Comment #{parent_index + 1} ({parent_anon_id})**\n"
            f"> {parent_text_preview}\n\n"
            "Please type your reply now. It will be posted anonymously."
        )


# -------------------------
# COMMENT/REPLY FLOW SUBMIT (Receiving the comment/reply text) (No change)
# -------------------------
# ... (handle_comment_submission remains unchanged)
@dp.message(CommentStates.waiting_for_submission)
async def handle_comment_submission(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    conf_id = data.get("target_conf_id")
    # parent_index is the 0-based index of the comment being replied to. -1 means top-level.
    parent_index = data.get("parent_index", -1) 
    comment_text = msg.text
    
    if not conf_id:
        await msg.answer("❌ Submission failed. Please click 'Add Comment'/'Reply' again.")
        await state.clear()
        return

    if not comment_text or len(comment_text.strip()) < 5:
        await msg.answer("Your comment/reply is too short (min 5 characters). Please try again.")
        return
    
    if len(comment_text) > 4000:
        await msg.answer("Your comment/reply is too long (max 4000 characters). Please shorten it.")
        return

    try:
        doc = conf_col.find_one({"_id": ObjectId(conf_id), "approved": True})
        if not doc:
            await msg.answer("❌ Confession no longer available.")
            await state.clear()
            return
            
        conf_number = doc.get('number', 'N/A')
        conf_author_id = doc["user_id"]
        
        # --- 1. Handle Reply Prefix and Parent Notification ---
        parent_author_id = None
        parent_anon_id_for_display = None 
        
        if parent_index != -1:
            comments = doc.get("comments", [])
            parent_comment = comments[parent_index]
            parent_author_id = parent_comment["user_id"]
            
            # Generate Anon ID for visual prefix
            anon_map = generate_anon_id_map(comments)
            parent_anon_id_for_display = anon_map.get(parent_author_id, f"Anon {parent_index + 1}")
            
            # Notify Parent Comment Author (if it's a reply and not the current user)
            if parent_author_id != msg.from_user.id:
                bot_url = f"https://t.me/{BOT_USERNAME}?start=comment_{conf_id}"
                notification_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="➡️ View Confession Thread", url=bot_url)]
                ])
                await send_notification(
                    parent_author_id,
                    f"↩️ **Notification:** A reply has been sent to your comment under confession **#{conf_number}**.",
                    notification_kb
                )
        
        # 2. Build the new comment object 
        new_comment = {
            "user_id": msg.from_user.id, 
            "text": comment_text,
            "created_at": datetime.now(UTC),
            "likes": 0, 
            "dislikes": 0, 
            "comment_voters": {},
            # Store the Anon ID of the comment this is replying to for the visual prefix
            "replying_to_anon": parent_anon_id_for_display,
            # CRITICAL: Store the 0-based index of the parent comment
            "parent_index": parent_index 
        }

        # 3. Add comment/reply to the confession document
        conf_col.update_one(
            {"_id": ObjectId(conf_id), "approved": True},
            {"$push": {"comments": new_comment}}
        )
        
        # --- 4. Notify Confession Author (ONLY for new top-level comments) ---
        if parent_index == -1 and conf_author_id != msg.from_user.id:
            bot_url = f"https://t.me/{BOT_USERNAME}?start=comment_{conf_id}"
            notification_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➡️ View New Comment", url=bot_url)]
            ])
            await send_notification(
                conf_author_id, 
                f"🔔 **Notification:** New comment posted under your confession **#{conf_number}**.",
                notification_kb
            )
        
        # 5. Display updated view
        await show_confession_and_comments(msg, conf_id)

    except Exception as e:
        await msg.answer(f"⚠️ An error occurred while saving your submission. Please try again. ({e})")
    
    finally:
        await state.clear()


# -------------------------
# NEW: Comment Voting Logic (No change)
# -------------------------
# ... (cb_handle_comment_vote remains unchanged)
@dp.callback_query(lambda c: c.data and c.data.startswith('cmt_vote:'))
async def cb_handle_comment_vote(callback: types.CallbackQuery):
    await callback.answer()
    
    parts = callback.data.split(':')
    if len(parts) != 4:
        return
        
    _, vote_type, conf_id, comment_index_str = parts
    user_id = callback.from_user.id
    str_user_id = str(user_id)
    
    try:
        comment_index = int(comment_index_str)
        doc = conf_col.find_one({"_id": ObjectId(conf_id), "approved": True})
    except Exception:
        doc = None
        
    if not doc or not doc.get("approved"):
        await callback.answer("Confession not found or not approved.", show_alert=True)
        return

    comments = doc.get("comments", [])
    if comment_index < 0 or comment_index >= len(comments):
        await callback.answer("Invalid comment index.", show_alert=True)
        return
        
    comment = comments[comment_index]
    
    if comment.get("user_id") == user_id:
        await callback.answer("You cannot vote on your own comment.", show_alert=True)
        return
        
    voters = comment.get("comment_voters", {})
    current_vote = voters.get(str(user_id), 0) # Use str(user_id) for consistent key in dict
    
    vote_value = 1 if vote_type == "like" else -1
    
    new_likes = comment.get("likes", 0)
    new_dislikes = comment.get("dislikes", 0)
    karma_change = 0
    
    # --- Voting Logic (Same as post voting) ---
    if current_vote == 0:
        if vote_type == "like":
            new_likes += 1
        else:
            new_dislikes += 1
        voters[str_user_id] = vote_value
        karma_change = vote_value 
        
    elif current_vote == vote_value:
        if vote_type == "like":
            new_likes -= 1
        else:
            new_dislikes -= 1
        del voters[str_user_id]
        karma_change = -current_vote 
        
    else: # Switching vote
        if current_vote == 1:
            new_likes -= 1
        else:
            new_dislikes -= 1
        
        if vote_type == "like":
            new_likes += 1
        else:
            new_dislikes += 1
            
        voters[str_user_id] = vote_value
        karma_change = vote_value - current_vote 
    
    # 1. Update the specific comment within the array
    update_field = {
        f"comments.{comment_index}.likes": new_likes,
        f"comments.{comment_index}.dislikes": new_dislikes,
        f"comments.{comment_index}.comment_voters": voters
    }
    
    conf_col.update_one(
        {"_id": ObjectId(conf_id)},
        {"$set": update_field}
    )
    
    # 2. Update Karma for Comment Author 
    comment_author_id = comment["user_id"]
    if karma_change != 0:
        karma_col.update_one(
            {"_id": comment_author_id},
            {"$inc": {"karma": karma_change}},
            upsert=True
        )

    # 3. Send a new chain of messages (Crucial for real-time update in the desired format)
    # This also handles the case where the vote happened in the private chat view.
    if callback.message.chat.type == "private":
        await show_confession_and_comments(callback.message, conf_id)
    
    # Silent confirmation on the callback query
    await callback.answer(f"Comment vote recorded. Karma change for author: {karma_change}")


# -------------------------
# Karma System: Handle Post Votes (Like/Dislike) (No change)
# -------------------------
# ... (cb_handle_vote remains unchanged)
@dp.callback_query(lambda c: c.data and c.data.startswith('vote:'))
async def cb_handle_vote(callback: types.CallbackQuery):
    await callback.answer()
    
    parts = callback.data.split(':')
    if len(parts) != 3:
        return
        
    _, vote_type, conf_id = parts
    user_id = callback.from_user.id
    str_user_id = str(user_id)
    
    try:
        doc = conf_col.find_one({"_id": ObjectId(conf_id)})
    except Exception:
        doc = None
        
    if not doc or not doc.get("approved"):
        await callback.answer("Confession not found or not approved.", show_alert=True)
        return

    if doc.get("user_id") == user_id:
        await callback.answer("You cannot vote on your own confession.", show_alert=True)
        return
        
    voters = doc.get("voters", {})
    current_vote = voters.get(str_user_id, 0) 
    
    vote_value = 1 if vote_type == "like" else -1
    
    new_likes = doc.get("likes", 0)
    new_dislikes = doc.get("dislikes", 0)
    karma_change = 0
    
    if current_vote == 0:
        if vote_type == "like":
            new_likes += 1
        else:
            new_dislikes += 1
        voters[str_user_id] = vote_value
        karma_change = vote_value 
        
    elif current_vote == vote_value:
        if vote_type == "like":
            new_likes -= 1
        else:
            new_dislikes -= 1
        del voters[str_user_id]
        karma_change = -current_vote 
        
    else: 
        if current_vote == 1:
            new_likes -= 1
        else:
            new_dislikes -= 1
        
        if vote_type == "like":
            new_likes += 1
        else:
            new_dislikes += 1
            
        voters[str_user_id] = vote_value
        karma_change = vote_value - current_vote 

    # 1. Update Confession Document (Likes/Dislikes)
    conf_col.update_one(
        {"_id": ObjectId(conf_id)},
        {"$set": {"likes": new_likes, "dislikes": new_dislikes, "voters": voters}}
    )
    
    # 2. Update Karma for Confessor 
    confessor_id = doc["user_id"]
    if karma_change != 0:
        karma_col.update_one(
            {"_id": confessor_id},
            {"$inc": {"karma": karma_change}},
            upsert=True
        )

    # 3. Update the Reaction Keyboard on the Channel Message
    # CRITICAL: Rebuild the keyboard with the correct deep-link
    bot_url = f"https://t.me/{BOT_USERNAME}?start=comment_{conf_id}" 
    reaction_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"👍  ({new_likes})", callback_data=f"vote:like:{conf_id}"),
            InlineKeyboardButton(text=f"👎  ({new_dislikes})", callback_data=f"vote:dislike:{conf_id}")
        ],
        [
            # Re-use the new comment button logic
            InlineKeyboardButton(text="💬 View / Add Comment", url=bot_url)
        ]
    ])
    
    try:
        if doc.get("channel_message_id"):
             await bot.edit_message_reply_markup(
                chat_id=CHANNEL_ID, 
                message_id=doc["channel_message_id"],
                reply_markup=reaction_kb
            )
    except Exception:
        pass 
        
    # 4. Update the view in the private chat if the vote happened there
    if callback.message.chat.type == "private":
        await show_confession_and_comments(callback.message, conf_id)

    await callback.answer(f"Vote recorded. Karma change: {karma_change}")


# -------------------------
# Admin: build admin keyboard for a confession (No change)
# -------------------------
def admin_kb(conf_id: str):
# ... (admin_kb remains unchanged)
    conf_id_str = str(conf_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Approve", callback_data=f"ok:{conf_id_str}"),
            InlineKeyboardButton(text="❌ Reject", callback_data=f"no:{conf_id_str}")
        ],
        [
            InlineKeyboardButton(text="✉️ Reply", callback_data=f"reply:{conf_id_str}"),
            InlineKeyboardButton(text="🔎 View", callback_data=f"view:{conf_id_str}")
        ]
    ])
    return kb


# --------------------------------------------------------
# Profile Management (/profile, Edit Bio/Nickname/Emoji flow) (UPDATED)
# --------------------------------------------------------

# Main /profile command - handles initial view and 'profile_view' callback
@dp.message(Command("profile"))
@dp.callback_query(F.data == "profile_view")
async def cmd_profile_view(request: types.Message | types.CallbackQuery, state: FSMContext):
    
    if isinstance(request, types.CallbackQuery):
        msg = request.message
        user_id = request.from_user.id
        await request.answer()
    else:
        msg = request
        user_id = request.from_user.id
        
    if msg.chat.type != "private":
        if isinstance(request, types.Message):
            await msg.reply("Please use /profile in a private chat with me.")
        return

    # Clear FSM state when viewing the profile
    await state.clear()
        
    # Get profile data (creates default if non-existent)
    profile = get_user_profile(user_id)
    
    # Get Karma (from separate collection)
    karma_doc = karma_col.find_one({"_id": user_id})
    karma_score = karma_doc.get("karma", 0) if karma_doc else 0
    
    # Format the message using the new function
    profile_text = format_profile_message(profile, user_id, karma_score)
    
    # Keyboard for editing and leaderboard
    kb = get_profile_menu_keyboard()
    
    try:
        # Edit the message if it's a callback, send new if it's a command
        if isinstance(request, types.CallbackQuery):
            await msg.edit_text(profile_text, reply_markup=kb, parse_mode="Markdown")
        else:
            await msg.answer(profile_text, reply_markup=kb, parse_mode="Markdown")
    except TelegramAPIError:
        pass # Ignore 'message not modified' error

# Callback to show the edit menu
@dp.callback_query(F.data == "profile_edit")
async def cb_profile_edit(callback: types.CallbackQuery):
    await callback.answer("Choose what to edit.")
    kb = get_edit_profile_keyboard()
    try:
        await callback.message.edit_text(
            "⚙️ **Edit Your Profile**\n\nChoose an option below:",
            reply_markup=kb, 
            parse_mode="Markdown"
        )
    except TelegramAPIError:
        pass

# --------------------
# 1. Edit Nickname Flow
# --------------------
@dp.callback_query(F.data == "edit_nickname")
async def cb_edit_nickname_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ProfileStates.editing_nickname)
    await callback.message.edit_text(
        f"⭐ **Enter your new anonymous Nickname.**\n"
        f"Max {MAX_NICKNAME_LENGTH} characters. Use letters and numbers only."
    )

@dp.message(ProfileStates.editing_nickname)
async def handle_new_nickname(msg: types.Message, state: FSMContext):
    new_nickname = msg.text.strip()
    user_id = msg.from_user.id
    
    if not new_nickname or len(new_nickname) < 3:
        await msg.answer("Your nickname is too short (min 3 characters). Please try again.")
        return
    
    if len(new_nickname) > MAX_NICKNAME_LENGTH:
        await msg.answer(f"Your nickname is too long (max {MAX_NICKNAME_LENGTH} characters). Please shorten it.")
        return

    # Optional: Basic validation to disallow common profanities or restricted names
    if re.search(r'[^a-zA-Z0-9\s]', new_nickname):
        await msg.answer("Nickname can only contain letters, numbers, and spaces. Please try again.")
        return

    try:
        users_col.update_one(
            {"_id": user_id},
            {"$set": {"nickname": new_nickname, "updated_at": datetime.now(UTC)}},
            upsert=True
        )
        await state.clear()
        
        # Display updated profile view
        await cmd_profile_view(msg, state) # Use the unified function to display profile
        
    except Exception as e:
        await msg.answer(f"⚠️ An error occurred while saving your nickname. Please try again. ({e})")
        await state.clear()


# --------------------
# 2. Edit Bio Flow (UPDATED)
# --------------------
@dp.callback_query(F.data == "edit_bio")
async def cb_edit_bio_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ProfileStates.editing_bio)
    await callback.message.edit_text(
        "📝 **Enter your new bio.**\n"
        "Keep it concise (max 200 characters). This bio is *private*."
    )

@dp.message(ProfileStates.editing_bio)
async def handle_new_bio(msg: types.Message, state: FSMContext):
    new_bio = msg.text.strip()
    user_id = msg.from_user.id
    
    if not new_bio or len(new_bio) < 5:
        await msg.answer("Your bio is too short. Please send a bio of at least 5 characters.")
        return
    
    if len(new_bio) > 200:
        await msg.answer(f"Your bio is too long ({len(new_bio)} characters). Please keep it under 200 characters.")
        return

    try:
        users_col.update_one(
            {"_id": user_id},
            {"$set": {"bio": new_bio, "updated_at": datetime.now(UTC)}},
            upsert=True
        )
        await state.clear()
        
        # Display updated profile view
        await cmd_profile_view(msg, state)
        
    except Exception as e:
        await msg.answer(f"⚠️ An error occurred while saving your bio. Please try again. ({e})")
        await state.clear()

# --------------------
# 3. Change Emoji Flow
# --------------------
@dp.callback_query(F.data == "change_emoji")
async def cb_change_emoji_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ProfileStates.choosing_emoji)
    kb = get_emoji_picker_keyboard()
    
    await callback.message.edit_text(
        "🎨 **Choose Your Profile Emoji**\n\nSelect an emoji to represent your anonymous profile:",
        reply_markup=kb
    )

@dp.callback_query(ProfileStates.choosing_emoji, F.data.startswith("set_emoji:"))
async def handle_emoji_selection(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    
    # Data format: set_emoji:{emoji}
    emoji = callback.data.split(":")[1]
    user_id = callback.from_user.id
    
    if emoji not in EMOJI_OPTIONS:
        await callback.message.answer("Invalid emoji selected. Please choose from the list.")
        await state.clear()
        return
        
    try:
        users_col.update_one(
            {"_id": user_id},
            {"$set": {"emoji": emoji, "updated_at": datetime.now(UTC)}},
            upsert=True
        )
        await state.clear()
        
        # Display updated profile view
        # We need a Message object to call cmd_profile_view, so use the callback's message
        await cmd_profile_view(callback, state) 
        
    except Exception as e:
        await callback.message.answer(f"⚠️ An error occurred while saving your emoji. Please try again. ({e})")
        await state.clear()


# -------------------------
# Other Commands (No change)
# -------------------------
@dp.message(Command("help"))
async def cmd_help(msg: types.Message):
# ... (cmd_help remains unchanged)
    is_admin = msg.from_user.id in ADMIN_IDS
    admin_commands = (
        "\n\n**Admin Commands:**\n"
        "/pending - list pending confessions\n"
        "/toggle_auto_approve - switch between manual/auto approval\n"
    ) if is_admin else ""
    
    await msg.answer(
        "/confess - submit anonymous confession (private chat)\n"
        "/profile - manage your bio and view karma (private chat)\n"
        "/rules - channel rules\n"
        "/latest - show latest approved confessions\n"
        "/random - show a random approved confession\n"
        "/find <number> - find confession by number"
        f"{admin_commands}"
    )

@dp.message(Command("rules"))
async def cmd_rules(msg: types.Message):
# ... (cmd_rules remains unchanged)
    rules_text = (
        "📜 Channel rules:\n"
        "1. Stay anonymous — no sharing private info.\n"
        "2. No harassment, doxxing, or hate speech.\n"
        "3. No phone numbers, addresses, or identifying info.\n"
        "4. Admins may reject posts that break rules.\n"
        "Be kind. Be safe."
    )
    await msg.answer(rules_text)

@dp.message(Command("my_karma"))
async def cmd_my_karma(msg: types.Message):
# ... (cmd_my_karma remains unchanged)
    if msg.chat.type != "private":
        await msg.reply("Please use this command in a private chat with the bot.")
        return
    
    user_id = msg.from_user.id
    karma_doc = karma_col.find_one({"_id": user_id})
    karma_score = karma_doc.get("karma", 0) if karma_doc else 0
    
    await msg.answer(
        f"🌟 **Your Confession Karma**\n"
        f"You have accumulated **{karma_score} points** from your approved confessions.\n"
        "Points are earned when comments on your confession are liked (+1) or disliked (-1)."
    )

@dp.message(Command("toggle_auto_approve"))
async def cmd_toggle_auto_approve(msg: types.Message):
# ... (cmd_toggle_auto_approve remains unchanged)
    if msg.from_user.id not in ADMIN_IDS:
        await msg.reply("⛔ Admins only.")
        return
    
    global GLOBAL_AUTO_APPROVE
    
    new_state = not GLOBAL_AUTO_APPROVE
    
    settings_col.update_one(
        {"_id": "auto_approve_status"},
        {"$set": {"enabled": new_state}},
        upsert=True
    )
    
    GLOBAL_AUTO_APPROVE = new_state
    
    status = "ON (Auto-approval is now **ENABLED**)" if new_state else "OFF (Admins must **MANUALLY** approve)"
    await msg.reply(f"🤖 Confession auto-approval toggled to: **{status}**")

@dp.message(Command("pending"))
async def cmd_pending(msg: types.Message):
# ... (cmd_pending remains unchanged)
    if msg.from_user.id not in ADMIN_IDS:
        await msg.reply("⛔ Admins only.")
        return

    pending = list(conf_col.find({"approved": False}).sort("created_at", -1).limit(10))
    if not pending:
        await msg.reply("No pending confessions.")
        return
    
    await msg.answer(f"🔎 Found {len(pending)} pending confessions. Sending them now...")

    for p in pending:
        cid = str(p["_id"])
        tags_text = ' '.join([f'#{t.replace(" ", "_")}' for t in p.get("tags", [])])
        kb = admin_kb(cid)
        
        # Truncate text for admin caption if media is present (Limit 1024 characters)
        admin_text = p.get('text','')
        if p.get("media"):
            admin_text = truncate_text(admin_text, 1000)

        caption = f"📝 Pending Confession (ID: {cid}, User: {p.get('user_id')}, Tags: {tags_text})\n{admin_text}"
        
        try:
            if p.get("media"):
                await bot.send_photo(msg.from_user.id, p["media"], caption=caption, reply_markup=kb)
            else:
                await bot.send_message(msg.from_user.id, caption, reply_markup=kb)
        except Exception as e:
            await msg.answer(f"⚠️ Could not send confession {cid} to you. Error: {e}")

@dp.callback_query(lambda c: c.data and not c.data.startswith('vote:') and not c.data.startswith('comment_start:') and not c.data.startswith('cmt_vote:') and c.data != "profile_edit_bio" and not c.data.startswith("set_emoji:") and c.data not in ["profile_view", "profile_edit", "edit_nickname", "edit_bio", "change_emoji"])
async def cb_admin_actions(callback: types.CallbackQuery, state: FSMContext):
# ... (cb_admin_actions remains unchanged)
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Admin only.", show_alert=True)
        return

    data = callback.data
    
    if not data or ':' not in data:
        await callback.answer("Error: Invalid button data.")
        return
        
    parts = data.split(":", 1)
    
    if len(parts) != 2:
        await callback.answer("Error: Malformed button data.")
        return

    action, conf_id = parts
    
    if action not in ["ok", "no", "reply", "view"]:
        await callback.answer() 
        return

    try:
        doc = conf_col.find_one({"_id": ObjectId(conf_id)})
    except Exception:
        doc = None

    if action == "ok":
        if not doc or doc.get("approved"):
            await callback.answer("Confession not found or already processed.")
            await callback.message.edit_text("⚠️ This confession was already processed (approved or rejected).")
            return
            
        tags_text = ' '.join([f'#{t.replace(" ", "_")}' for t in doc.get("tags", [])])
        
        final_doc, success = await publish_confession(doc, tags_text)
        
        if success:
            await callback.message.edit_text(f"✅ Approved & posted as Confession #{final_doc['number']}.")
        else:
            await callback.message.edit_text("⚠️ Approved, but **failed to post** to the channel after multiple retries. The bot has sent you a separate **CRITICAL ERROR** notification.")
        
        await callback.answer("Approval process completed.")

    elif action == "no":
        if not doc or doc.get("approved"):
            await callback.answer("Not found or already processed.")
            return
        
        conf_col.delete_one({"_id": ObjectId(conf_id)})
        await callback.message.edit_text("❌ Rejected & deleted.")
        await callback.answer("Rejected!")

    elif action == "reply":
        if doc.get("approved"):
             await callback.answer("Cannot reply to an already approved/posted confession via this menu.")
             return
             
        await state.update_data(reply_to=conf_id)
        await state.set_state(ReplyStates.waiting_for_reply)
        await callback.message.answer("✉️ Send the reply message now (it will be forwarded privately to the confessor).")
        await callback.answer()

    elif action == "view":
        if not doc:
            await callback.answer("Not found.")
            return
        tags_text = ' '.join([f'#{t}' for t in doc.get("tags", [])])
        info = (
            f"ID: {conf_id}\nUser ID (hidden): {doc.get('user_id')}\n"
            f"Tags: {tags_text}\n"
            f"Created: {doc.get('created_at')}\n"
            f"Text: {doc.get('text')}"
        )
        await callback.message.answer(info)
        await callback.answer()

@dp.message(ReplyStates.waiting_for_reply)
async def admin_send_reply(msg: types.Message, state: FSMContext):
# ... (admin_send_reply remains unchanged)
    data = await state.get_data()
    conf_id = data.get("reply_to")
    if not conf_id:
        await msg.answer("No target confession. Please use the Reply button again.")
        await state.clear()
        return

    doc = conf_col.find_one({"_id": ObjectId(conf_id)})
    if not doc:
        await msg.answer("Confession not found.")
        await state.clear()
        return

    confessor_id = doc.get("user_id")
    try:
        await bot.send_message(confessor_id, f"✉️ Reply from admins regarding your confession:\n\n{msg.text}")
        await msg.answer("✅ Reply sent to confessor.")
    except (TelegramForbiddenError, TelegramBadRequest):
        await msg.answer("⚠️ Could not send reply to confessor (they may have blocked the bot).")
    except Exception:
        await msg.answer("⚠️ An unexpected error occurred while trying to send the reply.")

    await state.clear()

@dp.message(Command("find"))
async def cmd_find(msg: types.Message):
# ... (cmd_find remains unchanged)
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("Usage: /find <number>")
        return
    try:
        n = int(parts[1])
    except ValueError:
        await msg.answer("Provide a valid number like /find 12")
        return

    doc = conf_col.find_one({"number": n, "approved": True})
    if not doc:
        await msg.answer(f"Confession #{n} not found or not yet approved.")
        return
    
    # Use the new multi-message display logic
    await show_confession_and_comments(msg, str(doc["_id"]))


@dp.message(Command("latest"))
async def cmd_latest(msg: types.Message):
# ... (cmd_latest remains unchanged)
    docs = list(conf_col.find({"approved": True}).sort("approved_at", -1).limit(5))
    if not docs:
        await msg.answer("No approved confessions yet.")
        return
        
    await msg.answer(f"🔎 Showing latest {len(docs)} confessions. Use the /start comment_<id> link on any post in the channel to see its full view.")


@dp.message(Command("random"))
async def cmd_random(msg: types.Message):
# ... (cmd_random remains unchanged)
    count = conf_col.count_documents({"approved": True})
    if count == 0:
        await msg.answer("No approved confessions yet.")
        return
    skip = random.randint(0, max(0, count-1))
    docs = list(conf_col.find({"approved": True}).skip(skip).limit(1))
    if not docs:
        await msg.answer("No results.")
        return
    
    # Use the new multi-message display logic
    await show_confession_and_comments(msg, str(docs[0]["_id"]))


from aiohttp import web

# --- Keep-Alive Web Server for Render ---
async def handle(request):
    return web.Response(text="Confession bot is running...")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 10000)
    await site.start()


# --- Main entry point ---
async def main():
    print(f"Bot starting... Auto-Approval is: {'ENABLED' if GLOBAL_AUTO_APPROVE else 'DISABLED'}")

    # Run both bot and web server together
    await asyncio.gather(
        dp.start_polling(bot, drop_pending_updates=True),
        start_web_server()
    )


if __name__ == "__main__":
    asyncio.run(main())
