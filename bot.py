import os
import asyncio
import random
import time
from datetime import datetime, UTC, timedelta
from dotenv import load_dotenv
import re
from typing import Optional

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject 
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
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
ADMIN_IDS = [905781541,7001310702,6347817894] 
# Group ID (Current: 5099572645) - REPLACE WITH YOUR GROUP ID
GROUP_ID = int(-5099572645) 
# Channel ID (Current: -1003276055222) - REPLACE WITH YOUR CHANNEL ID
CHANNEL_ID = int(-1003276055222) 

CONFESSION_COOLDOWN = 60 * 5  # 5 minutes cooldown between submissions
NICKNAME_CHANGE_COOLDOWN = 30 * 24 * 60 * 60  # 30 days in seconds

# Tags based on your request images, for the user to choose
AVAILABLE_TAGS = [
    "Relationship", "Love", "Crush", "Family", "Friendship", "Sexual",
    "Life", "Motivation", "Advice", "Campus", "Dorm", "Experience",
    "Weird", "Funny", "Secret", "Money", "Health", "Mental",
    "Info", "Personal", "Business", "Religion", "Trauma", "Exam",
    "School", "Other"
]

# -------------------------
# DB Setup with Error Handling
# -------------------------
try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    print("✅ MongoDB connection successful")
    db = client["confessionBot"]
    conf_col = db["Confessions"]
    settings_col = db["Settings"] 
    karma_col = db["Karma"] 
    users_col = db["Users"] 
    blocked_col = db["BlockedUsers"]
    reports_col = db["UserReports"]
except Exception as e:
    print(f"❌ MongoDB connection failed: {e}")
    exit(1)

# Blocked users set (loaded at startup)
BLOCKED_USERS = set()

# Auto-block configuration
MAX_REPORTS_BEFORE_BLOCK = 5  # Auto-block after 5 reports

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

# -------------------------
# Anonymous Profile System
# -------------------------
def generate_anonymous_id(user_id: int) -> str:
    """Generates a consistent anonymous ID for a user without revealing their actual ID."""
    import hashlib
    # Create a hash of user_id + a salt to make it consistent but anonymous
    salt = "anonymous_salt_2024"  # Change this to any secret string
    hash_input = f"{user_id}{salt}".encode()
    hash_digest = hashlib.md5(hash_input).hexdigest()[:8]  # Use first 8 chars
    return f"anon_{hash_digest}"

# Add this mapping to store anonymous IDs (in memory, will reset on restart)
ANONYMOUS_ID_MAP = {}

def get_anonymous_profile_link(user_id: int) -> str:
    """Generates an anonymous profile link without revealing user ID."""
    if user_id not in ANONYMOUS_ID_MAP:
        ANONYMOUS_ID_MAP[user_id] = generate_anonymous_id(user_id)
    
    anonymous_id = ANONYMOUS_ID_MAP[user_id]
    return f"https://t.me/{BOT_USERNAME}?start=view_profile_{anonymous_id}"

def get_user_id_from_anonymous_id(anonymous_id: str) -> Optional[int]:
    """Retrieves user ID from anonymous ID (reverse lookup)."""
    for user_id, anon_id in ANONYMOUS_ID_MAP.items():
        if anon_id == anonymous_id:
            return user_id
    return None

# -------------------------
# Error Handler (FIXED)
# -------------------------
@dp.errors()
async def errors_handler(event: types.Update, exception: Exception):
    """
    Exceptions handler. Catches all exceptions within task factory tasks.
    """
    print(f"Exception while handling an update {event}: {exception}")
    return True

# -------------------------
# Block System Functions
# -------------------------
def load_blocked_users():
    """Load blocked users from database."""
    global BLOCKED_USERS
    try:
        blocked_users = blocked_col.find({})
        BLOCKED_USERS = {user["_id"] for user in blocked_users}
        print(f"Loaded {len(BLOCKED_USERS)} blocked users from database")
    except Exception as e:
        print(f"Error loading blocked users: {e}")
        BLOCKED_USERS = set()

# Block checking middleware
@dp.update.middleware()
async def block_check(handler, event: types.Update, data: dict):
    # Extract user_id based on update type
    user_id = None
    if event.message:
        user_id = event.message.from_user.id
    elif event.callback_query:
        user_id = event.callback_query.from_user.id
    
    # Check if user is blocked
    if user_id and user_id in BLOCKED_USERS:
        if event.message:
            await event.message.answer("🚫 You have been blocked from using this bot.")
        elif event.callback_query:
            await event.callback_query.answer("🚫 You have been blocked from using this bot.", show_alert=True)
        return
    
    return await handler(event, data)

# ------------------------
# User Profile Constants
# ------------------------
DEFAULT_NICKNAME = "anonymous"
DEFAULT_EMOJI = "👤"
# Updated emoji options as requested
EMOJI_OPTIONS = [
    "👤", "👨", "👩", "🧕", "🧑‍🎓", "🥸", "🧐", "😶‍🌫", "👽", "👾", 
    "🗣", "🧢", "🐉", "🍀", "✨", "🍿", "🎸", "🩼", "🔫", "🇪🇹",
    "🌟", "🚀", "💡", "🔮", "🎧", "🎨", "🎭", "🎵", "☕", "💻", 
    "🦊", "🦁", "🗿", "🦋", "👀", "🪑", "🎮", "🔥", "💧", "🌍"
]
MAX_NICKNAME_LENGTH = 24  # Updated to 24 as requested
MIN_NICKNAME_LENGTH = 3   # Added minimum length

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
    editing_bio = State()
    choosing_emoji = State()
    editing_privacy = State()

# NEW STATES FOR USER REQUESTS
class ReportStates(StatesGroup):
    waiting_for_report_reason = State()

class ChatRequestStates(StatesGroup):
    waiting_for_chat_request_message = State()

# NEW STATE FOR USER FEEDBACK/QUESTIONS
class UserQuestionStates(StatesGroup):
    waiting_for_question = State()

# NEW STATE FOR DELETION REQUESTS
class DeletionRequestStates(StatesGroup):
    waiting_for_deletion_reason = State()

# NEW STATE FOR RULES AGREEMENT
class RulesStates(StatesGroup):
    waiting_for_agreement = State()

last_confession_time = {}

# -------------------------
# Utility Functions
# -------------------------
def truncate_text(text: str, max_length: int) -> str:
    """Truncates text to max_length and adds ellipsis if cut."""
    if not text:
        return ""
    if len(text) > max_length:
        return text[:max_length - 3] + "..."
    return text

def generate_anon_id_map(comments):
    """Generates a mapping of user_id to anonymous display names."""
    anon_map = {}
    for i, comment in enumerate(comments):
        user_id = comment.get('user_id')
        if user_id not in anon_map:
            anon_map[user_id] = f"Anon {i + 1}"
    return anon_map

# -------------------------
# NEW: Vote Notification Function (FIXED with simple text links)
# -------------------------
async def send_vote_notification(user_id, confession_number, vote_type, is_comment=False, conf_id=None):
    """Sends notification when someone votes on a confession or comment."""
    if vote_type == "like":
        emoji = "👍"
        action = "liked"
    else:
        emoji = "👎" 
        action = "disliked"
    
    # Create simple text link without preview
    if conf_id:
        bot_url = f"https://t.me/{BOT_USERNAME}?start=comment_{conf_id}"
        confession_link = f"[Confession #{confession_number}]({bot_url})"
    else:
        confession_link = f"Confession #{confession_number}"
    
    if is_comment:
        message = f"{emoji} Someone {action} your comment on {confession_link}"
    else:
        message = f"{emoji} Someone {action} your {confession_link}"
    
    try:
        await bot.send_message(user_id, message, parse_mode="Markdown", disable_web_page_preview=True)
        return True
    except Exception as e:
        print(f"Failed to send vote notification to {user_id}: {e}")
        return False

# -------------------------
# Helper: Get User Profile Data (UPDATED)
# -------------------------
def get_user_profile(user_id):
    """Retrieves or initializes user profile data."""
    profile = users_col.find_one({"_id": user_id})
    if not profile:
        profile = {
            "_id": user_id,
            "nickname": DEFAULT_NICKNAME,
            "emoji": DEFAULT_EMOJI,
            "bio": "Default bio: Tell us about yourself!",
            "gender": "Not specified",
            "privacy_settings": {
                "bio_visible": False,
                "gender_visible": False
            },
            "aura_points": 0,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "last_nickname_change": None,
            "agreed_to_rules": False,  # NEW: Track rules agreement
        }
        users_col.insert_one(profile)
    return profile

def format_profile_message(profile: dict, user_id: int, karma_score: int):
    """Formats the profile message."""
    nickname = profile.get("nickname", DEFAULT_NICKNAME)
    emoji = profile.get("emoji", DEFAULT_EMOJI)
    bio = profile.get("bio", "No bio set 🤫")
    gender = profile.get("gender", "Not specified")
    privacy = profile.get("privacy_settings", {})
    
    aura = karma_score 

    bio_visibility = "🔓 Public" if privacy.get("bio_visible", False) else "🔒 Private"
    gender_visibility = "🔓 Public" if privacy.get("gender_visible", False) else "🔒 Private"

    # Check nickname change cooldown - FIXED TIMEZONE ISSUE
    last_change = profile.get("last_nickname_change")
    can_change_nickname = True
    cooldown_text = ""
    
    if last_change:
        # Ensure both datetimes are timezone-aware
        current_time = datetime.now(UTC)
        if last_change.tzinfo is None:
            # If last_change is naive, make it aware
            last_change = last_change.replace(tzinfo=UTC)
        
        time_since_change = (current_time - last_change).total_seconds()
        can_change_nickname = time_since_change >= NICKNAME_CHANGE_COOLDOWN

        if not can_change_nickname:
            days_left = int((NICKNAME_CHANGE_COOLDOWN - time_since_change) / (24 * 60 * 60)) + 1
            cooldown_text = f"\n\n⏳ You can change your nickname again in {days_left} days."

    return (
        f"{emoji} **{nickname}'s Profile**\n"
        f"🆔 User ID: `{user_id}`\n\n"
        f"✨ **Aura:** `{aura}` points (from post/comment voting)\n"
        f"⚧️ **Gender:** {gender} ({gender_visibility})\n\n"
        f"📝 **Bio:**\n"
        f"_{bio}_\n"
        f"({bio_visibility})"
        f"{cooldown_text}"
    )

def format_public_profile_message(profile: dict, karma_score: int):
    """Formats a public view of user profile (for others to see)."""
    nickname = profile.get("nickname", DEFAULT_NICKNAME)
    emoji = profile.get("emoji", DEFAULT_EMOJI)
    privacy = profile.get("privacy_settings", {})
    
    aura = karma_score
    gender = profile.get("gender", "Not specified")
    bio = profile.get("bio", "No bio set 🤫")
    
    # Only show bio and gender if user has set them to public
    bio_text = f"📝 **Bio:**\n_{bio}_\n\n" if privacy.get("bio_visible", False) else ""
    gender_text = f"⚧️ **Gender:** {gender}\n" if privacy.get("gender_visible", False) else ""
    
    return (
        f"{emoji} **{nickname}'s Public Profile**\n\n"
        f"✨ **Aura:** `{aura}` Aura\n"
        f"{gender_text}"
        f"{bio_text}"
    )

# ------------------------
# Keyboard Builders (UPDATED)
# ------------------------

def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Reply keyboard for main commands."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="📝 Confess"), types.KeyboardButton(text="👤 Profile")],
            [types.KeyboardButton(text="📋 Menu")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Choose an option or type /help"
    )

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for the main menu."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📝 Confess", callback_data="menu_confess")
    builder.button(text="👤 Profile", callback_data="menu_profile")
    builder.button(text="📋 Menu", callback_data="menu_more")
    builder.adjust(1)
    return builder.as_markup()

def get_more_menu_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for the more menu options."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📜 My Confessions", callback_data="menu_my_confessions")
    builder.button(text="💬 My Comments", callback_data="menu_my_comments")
    builder.button(text="🏆 Leaderboard", callback_data="menu_leaderboard")
    builder.button(text="❓ Ask Admins", callback_data="menu_ask")
    builder.button(text="📚 Rules", callback_data="menu_rules")
    builder.button(text="🆘 Help", callback_data="menu_help")
    builder.button(text="⬅️ Back", callback_data="menu_back")
    builder.adjust(1)
    return builder.as_markup()

def get_profile_menu_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for the main profile view."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Edit Profile", callback_data="profile_edit")
    builder.button(text="⬅️ Back to Menu", callback_data="menu_back")
    builder.adjust(1)
    return builder.as_markup()

def get_edit_profile_keyboard(profile: dict) -> InlineKeyboardMarkup:
    """Keyboard for the profile editing menu."""
    builder = InlineKeyboardBuilder()
    
    # Check nickname change cooldown - FIXED TIMEZONE ISSUE
    last_change = profile.get("last_nickname_change")
    can_change_nickname = True
    
    if last_change:
        # Ensure both datetimes are timezone-aware
        current_time = datetime.now(UTC)
        if last_change.tzinfo is None:
            # If last_change is naive, make it aware
            last_change = last_change.replace(tzinfo=UTC)
        
        time_since_change = (current_time - last_change).total_seconds()
        can_change_nickname = time_since_change >= NICKNAME_CHANGE_COOLDOWN
    
    nickname_text = "⭐ Edit Nickname"
    if not can_change_nickname:
        days_left = int((NICKNAME_CHANGE_COOLDOWN - time_since_change) / (24 * 60 * 60)) + 1
        nickname_text = f"⭐ Edit Nickname ({days_left}d)"
    
    builder.button(text=nickname_text, callback_data="edit_nickname")
    builder.button(text="📝 Edit Bio", callback_data="edit_bio")
    builder.button(text="🎨 Change Emoji", callback_data="change_emoji")
    builder.button(text="⚧️ Set Gender", callback_data="set_gender")
    builder.button(text="🔒 Privacy Settings", callback_data="privacy_settings")
    builder.button(text="⬅️ Back to Profile", callback_data="profile_view")
    builder.adjust(1)
    return builder.as_markup()

def get_privacy_settings_keyboard(profile: dict) -> InlineKeyboardMarkup:
    """Keyboard for privacy settings."""
    privacy = profile.get("privacy_settings", {})
    bio_visible = privacy.get("bio_visible", False)
    gender_visible = privacy.get("gender_visible", False)
    
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"📝 Bio: {'🔓 Public' if bio_visible else '🔒 Private'}", 
        callback_data="toggle_bio_privacy"
    )
    builder.button(
        text=f"⚧️ Gender: {'🔓 Public' if gender_visible else '🔒 Private'}", 
        callback_data="toggle_gender_privacy"
    )
    builder.button(text="⬅️ Back to Edit Menu", callback_data="profile_edit")
    builder.adjust(1)
    return builder.as_markup()

def get_gender_selection_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for gender selection."""
    builder = InlineKeyboardBuilder()
    builder.button(text="👨 Male", callback_data="gender_male")
    builder.button(text="👩 Female", callback_data="gender_female")
    builder.button(text="🔫 AKA47", callback_data="gender_aka47")
    builder.button(text="🙈 Prefer not to say", callback_data="gender_not_say")
    builder.button(text="⬅️ Back to Edit Menu", callback_data="profile_edit")
    builder.adjust(2)
    return builder.as_markup()

def get_emoji_picker_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for selecting a profile emoji."""
    builder = InlineKeyboardBuilder()
    for emoji in EMOJI_OPTIONS:
        builder.button(text=emoji, callback_data=f"set_emoji:{emoji}")
    builder.button(text="⬅️ Back to Edit Menu", callback_data="profile_edit")
    builder.adjust(5)
    return builder.as_markup()

def get_user_profile_keyboard(target_user_id: int, viewer_user_id: int) -> InlineKeyboardMarkup:
    """Keyboard for viewing another user's profile with block option for admins."""
    builder = InlineKeyboardBuilder()
    
    # Only show report and chat request if not viewing own profile
    if target_user_id != viewer_user_id:
        builder.button(text="🚨 Report User", callback_data=f"report_user:{target_user_id}")
        builder.button(text="💬 Request Chat", callback_data=f"request_chat:{target_user_id}")
        
        # Add block button for admins
        if viewer_user_id in ADMIN_IDS:
            if target_user_id in BLOCKED_USERS:
                builder.button(text="✅ Unblock User", callback_data=f"admin_unblock:{target_user_id}")
            else:
                builder.button(text="🚫 Block User", callback_data=f"admin_block:{target_user_id}")
    
    builder.button(text="⬅️ Back", callback_data="menu_back")
    builder.adjust(1)
    return builder.as_markup()

def get_report_confirmation_keyboard(target_user_id: int) -> InlineKeyboardMarkup:
    """Keyboard for confirming user report."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Confirm Report", callback_data=f"confirm_report:{target_user_id}")
    builder.button(text="❌ Cancel", callback_data="cancel_report")
    builder.adjust(1)
    return builder.as_markup()

def get_chat_request_confirmation_keyboard(target_user_id: int) -> InlineKeyboardMarkup:
    """Keyboard for confirming chat request."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Send Request", callback_data=f"send_chat_request:{target_user_id}")
    builder.button(text="❌ Cancel", callback_data="cancel_chat_request")
    builder.adjust(1)
    return builder.as_markup()

def get_chat_request_response_keyboard(request_id: str, requester_id: int) -> InlineKeyboardMarkup:
    """Keyboard for responding to chat request."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Accept", callback_data=f"accept_chat_request:{request_id}:{requester_id}")
    builder.button(text="❌ Decline", callback_data=f"decline_chat_request:{request_id}")
    builder.adjust(1)
    return builder.as_markup()

def get_my_confessions_keyboard(confessions: list, page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Keyboard for my confessions pagination."""
    builder = InlineKeyboardBuilder()
    
    # Add deletion buttons for each confession
    for confession in confessions:
        conf_id = str(confession["_id"])
        conf_number = confession.get("number", "N/A")
        builder.button(
            text=f"🗑️ Request Deletion for #{conf_number}", 
            callback_data=f"request_deletion:{conf_id}"
        )
    
    # Pagination buttons
    if total_pages > 1:
        if page > 1:
            builder.button(text="⬅️ Back", callback_data=f"my_confessions:{page-1}")
        builder.button(text=f"Page {page}/{total_pages}", callback_data="noop")
        if page < total_pages:
            builder.button(text="Next ➡️", callback_data=f"my_confessions:{page+1}")
    
    builder.button(text="⬅️ Back to Menu", callback_data="menu_back")
    builder.adjust(1)
    return builder.as_markup()

def get_my_comments_keyboard(comments_data: list, page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Keyboard for my comments pagination."""
    builder = InlineKeyboardBuilder()
    
    # Pagination buttons
    if total_pages > 1:
        if page > 1:
            builder.button(text="⬅️ Back", callback_data=f"my_comments:{page-1}")
        builder.button(text=f"Page {page}/{total_pages}", callback_data="noop")
        if page < total_pages:
            builder.button(text="Next ➡️", callback_data=f"my_comments:{page+1}")
    
    builder.button(text="⬅️ Back to Menu", callback_data="menu_back")
    builder.adjust(2)
    return builder.as_markup()

def get_deletion_confirmation_keyboard(conf_id: str) -> InlineKeyboardMarkup:
    """Keyboard for confirming deletion request."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Confirm Deletion Request", callback_data=f"confirm_deletion:{conf_id}")
    builder.button(text="❌ Cancel", callback_data="cancel_deletion")
    builder.adjust(1)
    return builder.as_markup()

def get_rules_agreement_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for rules agreement."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ I Agree to the Rules", callback_data="agree_rules")
    builder.adjust(1)
    return builder.as_markup()

# -------------------------
# Comment Display Functions (UPDATED with anonymous links and confession author detection)
# -------------------------

def organize_comments_into_threads(comments):
    """Organizes comments into threaded structure with unlimited nesting."""
    # Create a map of comments by their index
    comment_map = {i: comment for i, comment in enumerate(comments)}
    
    # Build tree structure
    def build_tree(parent_index=-1):
        tree = []
        for i, comment in comment_map.items():
            if comment.get('parent_index') == parent_index:
                node = {
                    'comment': comment,
                    'replies': build_tree(i)  # Recursively build replies
                }
                tree.append(node)
        return tree
    
    return build_tree()

async def send_comment_tree(msg: types.Message, parent_message_id: int, tree: list, conf_id: str, viewer_id: int, depth=0):
    """Recursively sends comment tree with proper threading."""
    for node in tree:
        comment = node['comment']
        replies = node['replies']
        
        # Send the comment
        comment_msg = await send_single_comment(
            msg, parent_message_id, comment, conf_id, viewer_id, is_reply=(depth > 0)
        )
        
        if not comment_msg:
            continue
            
        # Recursively send replies with increased depth
        if replies:
            await send_comment_tree(msg, comment_msg.message_id, replies, conf_id, viewer_id, depth + 1)

async def send_single_comment(msg: types.Message, reply_to_id: int, comment: dict, conf_id: str, viewer_id: int, is_reply: bool = False):
    """Sends a single comment with proper formatting in one message."""
    comment_author_id = comment.get('user_id')
    profile = get_user_profile(comment_author_id)
    karma_doc = karma_col.find_one({"_id": comment_author_id}) or {}
    aura_points = karma_doc.get('karma', 0)
    
    nickname = profile.get("nickname", DEFAULT_NICKNAME)
    emoji = profile.get("emoji", DEFAULT_EMOJI)
    
    c_likes = comment.get('likes', 0)
    c_dislikes = comment.get('dislikes', 0)
    
    # Create keyboard for the comment
    comment_kb = get_comment_keyboard(conf_id, comment, viewer_id, comment_author_id, c_likes, c_dislikes)
    
    # Check if comment author is the confession author
    doc = conf_col.find_one({"_id": ObjectId(conf_id)})
    is_confession_author = doc and doc.get("user_id") == comment_author_id
    
    # Create profile display - show "Confession Author" if it's their own post
    if is_confession_author:
        profile_display = f"**📝 Confession Author** ⚡{aura_points} Aura"
    else:
        # Use anonymous profile link
        profile_link = get_anonymous_profile_link(comment_author_id)
        profile_display = f"[{emoji} {nickname}]({profile_link}) ⚡{aura_points} Aura"
    
    try:
        # Handle different content types - ALL in one message
        if comment.get('sticker_id'):
            # For stickers: send sticker with caption containing profile info
            caption = f"{profile_display}\n🎭 Sticker: {comment.get('sticker_emoji', '')}"
            if is_reply:
                return await msg.answer_sticker(
                    comment['sticker_id'],
                    caption=caption,
                    reply_to_message_id=reply_to_id,
                    reply_markup=comment_kb,
                    parse_mode="Markdown"
                )
            else:
                return await msg.answer_sticker(
                    comment['sticker_id'],
                    caption=caption,
                    reply_markup=comment_kb,
                    parse_mode="Markdown"
                )
                
        elif comment.get('animation_id'):
            # For GIFs: send animation with caption containing profile info
            caption = f"{profile_display}\n🎬 GIF"
            if is_reply:
                return await msg.answer_animation(
                    comment['animation_id'],
                    caption=caption,
                    reply_to_message_id=reply_to_id,
                    reply_markup=comment_kb,
                    parse_mode="Markdown"
                )
            else:
                return await msg.answer_animation(
                    comment['animation_id'],
                    caption=caption,
                    reply_markup=comment_kb,
                    parse_mode="Markdown"
                )
                
        elif comment.get('text'):
            # For text comments: include profile info and text in one message
            comment_text = f"{profile_display}\n{comment.get('text', '')}"
            
            if is_reply:
                return await msg.answer(
                    comment_text,
                    reply_to_message_id=reply_to_id,
                    reply_markup=comment_kb,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
            else:
                return await msg.answer(
                    comment_text,
                    reply_markup=comment_kb,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
        else:
            # Fallback for other media types
            comment_text = f"{profile_display}\n📎 Media content"
            if is_reply:
                return await msg.answer(
                    comment_text,
                    reply_to_message_id=reply_to_id,
                    reply_markup=comment_kb,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
            else:
                return await msg.answer(
                    comment_text,
                    reply_markup=comment_kb,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
                
    except Exception as e:
        print(f"Error sending comment: {e}")
        return None

def get_comment_keyboard(conf_id: str, comment: dict, viewer_id: int, comment_author_id: int, likes: int, dislikes: int):
    """Creates keyboard for a comment with voting and user actions."""
    comment_index = comment.get('_index', 0)
    
    builder = InlineKeyboardBuilder()
    
    # Voting buttons with counts ON THE BUTTONS
    builder.button(text=f"👍 {likes}", callback_data=f"cmt_vote:like:{conf_id}:{comment_index}")
    builder.button(text=f"👎 {dislikes}", callback_data=f"cmt_vote:dislike:{conf_id}:{comment_index}")
    
    # Reply button
    builder.button(text="↩️ Reply", callback_data=f"comment_start:{conf_id}:{comment_index}")
    
    builder.adjust(3)
    return builder.as_markup()

async def show_confession_and_comments(msg: types.Message, conf_id: str):
    """
    Fetches, formats, and displays the confession post and its comments in a clean flat format.
    """
    user_id = msg.from_user.id
    
    try:
        doc = conf_col.find_one({"_id": ObjectId(conf_id), "approved": True})
    except Exception:
        doc = None
        
    if not doc:
        await msg.answer("❌ Confession not found or not approved.")
        return

    # --- 1. Format the Main Confession Text ---
    # REMOVED tags from the main confession display in bot
    main_confession_text = doc.get('text', '') 
    
    conf_text = (
        f"**📜 Confession #{doc.get('number')}**\n\n"
        f"{main_confession_text}"
    )

    # --- 2. Build the Keyboard for the Main Post ---
    comments = doc.get("comments", [])
    full_kb_builder = InlineKeyboardBuilder()
    
    full_kb_builder.row(
        InlineKeyboardButton(text="✍️ Add Comment", callback_data=f"comment_start:{conf_id}:-1")
    )

    is_confessor = doc.get("user_id") == user_id
    if not is_confessor:
        full_kb_builder.row(
            InlineKeyboardButton(text=f"👍 {doc.get('likes', 0)}", callback_data=f"vote:like:{conf_id}"),
            InlineKeyboardButton(text=f"👎 {doc.get('dislikes', 0)}", callback_data=f"vote:dislike:{conf_id}")
        )
    
    kb = full_kb_builder.as_markup()
    
    # --- 3. Send the Main Message ---
    main_message = None
    try:
        if doc.get("media"):
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

    # --- 4. Send Comments in Clean Flat Format ---
    if comments:
        # Organize comments into tree structure (supports unlimited nesting)
        comment_tree = organize_comments_into_threads(comments)
        
        # Send the comment tree starting from the main message
        await send_comment_tree(msg, main_message_id, comment_tree, conf_id, user_id)
        
        # Show message if there are too many comments
        if len(comments) > 20:
            await msg.answer(
                f"*... and {len(comments) - 20} more comments not shown.*",
                reply_to_message_id=main_message_id,
                parse_mode="Markdown"
            )

# -------------------------
# Rules Agreement Middleware (FIXED - directs to bot instead of showing rules)
# -------------------------

@dp.update.middleware()
async def rules_agreement_check(handler, event: types.Update, data: dict):
    """Check if user has agreed to rules before processing any commands."""
    # Extract user_id based on update type
    user_id = None
    if event.message:
        user_id = event.message.from_user.id
    elif event.callback_query:
        user_id = event.callback_query.from_user.id
    
    if user_id:
        # Check if user has agreed to rules
        profile = get_user_profile(user_id)
        if not profile.get("agreed_to_rules", False):
            # Allow essential commands
            if event.message and event.message.text:
                allowed_commands = ['/start', '/help', '/rules', '/ping', '/test']
                if any(event.message.text.startswith(cmd) for cmd in allowed_commands):
                    return await handler(event, data)
                elif event.message.text == '✅ I Agree to the Rules':
                    return await handler(event, data)
            elif event.callback_query and event.callback_query.data == 'agree_rules':
                return await handler(event, data)
            else:
                # User hasn't agreed to rules - direct them to the bot
                if event.message:
                    await event.message.answer(
                        "📜 **Welcome to Confession Bot!**\n\n"
                        "To use this bot and access all features, you need to agree to our community rules first.\n\n"
                        f"Please [click here to open the bot](https://t.me/{BOT_USERNAME}) and agree to the rules to continue.",
                        parse_mode="Markdown",
                        disable_web_page_preview=True
                    )
                elif event.callback_query:
                    await event.callback_query.answer(
                        "Please open the bot and agree to the rules first.", 
                        show_alert=True
                    )
                return
    
    return await handler(event, data)

async def show_rules_agreement(msg: types.Message):
    """Shows rules and requires agreement."""
    rules_text = (
        "📜 **Community Rules & Agreement**\n\n"
        "To use this bot, you must agree to the following rules:\n\n"
        "1.  **Stay Relevant:** This space is mainly for sharing confessions, experiences, and thoughts.\n"
        "    - Avoid using it just to ask random questions you could easily Google or ask in the right place.\n"
        "    - Some Academic-related questions may be approved if they benefit the community.\n\n"
        "2.  **Respectful Communication:** Sensitive topics (political, religious, cultural, etc.) are allowed but must be discussed with respect.\n\n"
        "3.  **No Harmful Content:** You may mention names, but at your own risk.\n"
        "    - The bot and admins are not responsible for any consequences.\n"
        "    - If someone mentioned requests removal, their name will be taken down.\n\n"
        "4.  **Names & Responsibility:** Do not share personal identifying information about yourself or others.\n\n"
        "5.  **Anonymity & Privacy:** Don't reveal private details of others (contacts, address, etc.) without consent.\n\n"
        "6.  **Constructive Environment:** Keep confessions genuine. Avoid spam, trolling, or repeated submissions.\n"
        "    - Respect moderators' decisions on approvals, edits, or removals.\n\n"
        "By clicking 'I Agree', you acknowledge that you have read and will follow these rules."
    )
    
    kb = get_rules_agreement_keyboard()
    await msg.answer(rules_text, reply_markup=kb, parse_mode="Markdown")

# -------------------------
# NEW: Debug Commands
# -------------------------

@dp.message(Command("ping"))
async def cmd_ping(msg: types.Message):
    """Test if bot is responding."""
    await msg.answer("🏓 Pong! Bot is responding.")

@dp.message(Command("test"))
async def cmd_test(msg: types.Message):
    """Test basic functionality."""
    user_id = msg.from_user.id
    profile = get_user_profile(user_id)
    await msg.answer(
        f"✅ Bot is working!\n"
        f"User ID: {user_id}\n"
        f"Agreed to rules: {profile.get('agreed_to_rules', False)}\n"
        f"Profile exists: True"
    )

# -------------------------
# User Profile Viewing System (UPDATED with anonymous deep link support)
# -------------------------

@dp.message(Command("start"))
async def cmd_start(msg: types.Message, command: CommandObject, state: FSMContext): 
    # Use command.args to safely get the payload part of the deep link
    payload = command.args
    
    # Check if user has agreed to rules
    user_id = msg.from_user.id
    profile = get_user_profile(user_id)
    
    if not profile.get("agreed_to_rules", False):
        await show_rules_agreement(msg)
        return
    
    if payload:
        # Deep link detected, check if it's a comment link
        payload_match = re.match(r'^comment_([0-9a-fA-F]+)$', payload)
        if payload_match:
            # Deep link is for viewing comments
            conf_id = payload_match.group(1)
            await state.clear() 
            
            # Send a new chain of messages for the post view
            await msg.answer(
                "Loading post view... Note: Voting/actions will generate a new message chain for up-to-date information.",
                reply_markup=get_main_reply_keyboard()
            )
            await show_confession_and_comments(msg, conf_id)
            return
        
        # Check if it's a profile view deep link with anonymous ID
        profile_match = re.match(r'^view_profile_(anon_[a-f0-9]+)$', payload)
        if profile_match:
            # Deep link is for viewing a profile with anonymous ID
            anonymous_id = profile_match.group(1)
            target_user_id = get_user_id_from_anonymous_id(anonymous_id)
            
            if target_user_id:
                await state.clear()
                # Show the public profile
                await show_public_profile(msg, target_user_id)
                return
            else:
                await msg.answer("❌ Profile not found or link expired.")

    # Default /start behavior - show main menu with reply keyboard
    await show_main_menu(msg)

@dp.callback_query(F.data == "agree_rules")
async def cb_agree_rules(callback: types.CallbackQuery):
    """Handle user agreement to rules."""
    user_id = callback.from_user.id
    
    # Update user profile to mark agreement
    users_col.update_one(
        {"_id": user_id},
        {"$set": {"agreed_to_rules": True, "updated_at": datetime.now(UTC)}}
    )
    
    await callback.answer("Thank you for agreeing to the rules!")
    await callback.message.answer(
        "✅ **Thank you for agreeing to the rules!**\n\n"
        "You can now use all features of the bot. Welcome to the community! 🤗",
        reply_markup=get_main_reply_keyboard()
    )

async def show_main_menu(msg: types.Message):
    """Shows the main menu."""
    await msg.answer(
        "🤖 **Confession Bot**\n\n"
        "Welcome! Choose an option below:",
        reply_markup=get_main_reply_keyboard()
    )

async def show_public_profile(msg: types.Message, target_user_id: int):
    """Shows public profile of another user."""
    viewer_id = msg.from_user.id
    
    # Get target user's profile
    profile = get_user_profile(target_user_id)
    karma_doc = karma_col.find_one({"_id": target_user_id}) or {}
    karma_score = karma_doc.get('karma', 0)
    
    # Format public profile
    profile_text = format_public_profile_message(profile, karma_score)
    kb = get_user_profile_keyboard(target_user_id, viewer_id)
    
    await msg.answer(profile_text, reply_markup=kb, parse_mode="Markdown")

# -------------------------
# Reply Keyboard Handlers
# -------------------------

@dp.message(F.text == "📝 Confess")
async def handle_confess_button(msg: types.Message, state: FSMContext):
    """Handles the Confess button from reply keyboard."""
    await cmd_confess_start(msg, state)

@dp.message(F.text == "👤 Profile")
async def handle_profile_button(msg: types.Message, state: FSMContext):
    """Handles the Profile button from reply keyboard."""
    await cmd_profile_view(msg, state)

@dp.message(F.text == "📋 Menu")
async def handle_menu_button(msg: types.Message):
    """Handles the Menu button from reply keyboard."""
    await show_more_menu(msg)

async def show_more_menu(msg: types.Message):
    """Shows the more menu options."""
    await msg.answer(
        "📋 **Menu Options**\n\n"
        "Choose an option:",
        reply_markup=get_more_menu_keyboard()
    )

# -------------------------
# Menu System
# -------------------------

@dp.callback_query(F.data == "menu_back")
async def cb_menu_back(callback: types.CallbackQuery):
    """Goes back to main menu."""
    await callback.answer()
    await show_main_menu(callback.message)

@dp.callback_query(F.data == "menu_more")
async def cb_menu_more(callback: types.CallbackQuery):
    """Shows more menu options."""
    await callback.answer()
    await show_more_menu(callback.message)

@dp.callback_query(F.data == "menu_confess")
async def cb_menu_confess(callback: types.CallbackQuery, state: FSMContext):
    """Starts confession process from menu."""
    await callback.answer()
    await cmd_confess_start(callback.message, state)

@dp.callback_query(F.data == "menu_profile")
async def cb_menu_profile(callback: types.CallbackQuery, state: FSMContext):
    """Shows profile from menu."""
    await callback.answer()
    await cmd_profile_view(callback.message, state)

@dp.callback_query(F.data == "menu_leaderboard")
async def cb_menu_leaderboard(callback: types.CallbackQuery):
    """Shows leaderboard from menu."""
    await callback.answer()
    await cmd_leaderboard(callback.message)

@dp.callback_query(F.data == "menu_ask")
async def cb_menu_ask(callback: types.CallbackQuery, state: FSMContext):
    """Starts ask process from menu."""
    await callback.answer()
    await cmd_ask_question(callback.message, state)

@dp.callback_query(F.data == "menu_rules")
async def cb_menu_rules(callback: types.CallbackQuery):
    """Shows rules from menu."""
    await callback.answer()
    await cmd_rules(callback.message)

@dp.callback_query(F.data == "menu_help")
async def cb_menu_help(callback: types.CallbackQuery):
    """Shows help from menu."""
    await callback.answer()
    await cmd_help(callback.message)

@dp.callback_query(F.data == "menu_my_confessions")
async def cb_menu_my_confessions(callback: types.CallbackQuery):
    """Shows user's confessions from menu."""
    await callback.answer()
    await cmd_my_confessions(callback.message)

@dp.callback_query(F.data == "menu_my_comments")
async def cb_menu_my_comments(callback: types.CallbackQuery):
    """Shows user's comments from menu."""
    await callback.answer()
    await cmd_my_comments(callback.message)

# -------------------------
# My Confessions System (FIXED - proper user_id filtering)
# -------------------------

@dp.message(Command("my_confessions"))
async def cmd_my_confessions(msg: types.Message, page: int = 1):
    """Shows user's confessions with pagination."""
    if msg.chat.type != "private":
        await msg.reply("Please use this command in a private chat with the bot.")
        return
    
    user_id = msg.from_user.id
    page_size = 5
    
    # FIXED: Proper query to get user's confessions
    skip = (page - 1) * page_size
    confessions = list(conf_col.find({"user_id": user_id})
                      .sort("created_at", -1)
                      .skip(skip)
                      .limit(page_size))
    
    total_confessions = conf_col.count_documents({"user_id": user_id})
    total_pages = (total_confessions + page_size - 1) // page_size if total_confessions > 0 else 1
    
    if not confessions:
        await msg.answer(
            "📜 **Your Confessions**\n\n"
            "You haven't submitted any confessions yet.\n\n"
            "Use the 'Confess' button to submit your first confession!",
            reply_markup=get_my_confessions_keyboard([], page, total_pages)
        )
        return
    
    # Format confessions list
    confessions_text = "📜 **Your Confessions**\n\n"
    
    for i, confession in enumerate(confessions):
        status = "✅ Approved" if confession.get("approved") else "⏳ Pending"
        conf_number = confession.get("number", "N/A")
        conf_text = truncate_text(confession.get('text', ''), 50)
        
        confessions_text += f"**ID: #{conf_number}** ({status})\n"
        confessions_text += f'"{conf_text}"\n\n'
    
    confessions_text += f"**Page {page}/{total_pages}**"
    
    await msg.answer(
        confessions_text,
        reply_markup=get_my_confessions_keyboard(confessions, page, total_pages)
    )

@dp.callback_query(F.data.startswith("my_confessions:"))
async def cb_my_confessions_page(callback: types.CallbackQuery):
    """Handles my confessions pagination."""
    await callback.answer()
    
    try:
        page = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        page = 1
    
    await cmd_my_confessions(callback.message, page)

@dp.callback_query(F.data.startswith("request_deletion:"))
async def cb_request_deletion_start(callback: types.CallbackQuery, state: FSMContext):
    """Starts the deletion request process."""
    await callback.answer()
    
    try:
        conf_id = callback.data.split(":")[1]
    except (ValueError, IndexError):
        await callback.answer("Invalid confession.")
        return
    
    # Get confession details
    confession = conf_col.find_one({"_id": ObjectId(conf_id)})
    if not confession:
        await callback.answer("Confession not found.")
        return
    
    await state.update_data(
        deletion_conf_id=conf_id,
        deletion_conf_number=confession.get("number", "N/A")
    )
    
    kb = get_deletion_confirmation_keyboard(conf_id)
    await callback.message.answer(
        f"🗑️ **Request Deletion for Confession #{confession.get('number', 'N/A')}**\n\n"
        "Please provide a reason for deletion:",
        reply_markup=kb
    )
    await state.set_state(DeletionRequestStates.waiting_for_deletion_reason)

@dp.callback_query(F.data.startswith("confirm_deletion:"))
async def cb_confirm_deletion(callback: types.CallbackQuery, state: FSMContext):
    """Confirms and sends deletion request to admins."""
    await callback.answer()
    
    try:
        conf_id = callback.data.split(":")[1]
    except (ValueError, IndexError):
        await callback.answer("Invalid confession.")
        return
    
    data = await state.get_data()
    reason = data.get('deletion_reason', 'No reason provided')
    requester_id = callback.from_user.id
    conf_number = data.get('deletion_conf_number', 'N/A')
    
    # Send deletion request to all admins
    deletion_text = (
        f"🗑️ **DELETION REQUEST**\n\n"
        f"📝 **Confession ID:** `{conf_id}`\n"
        f"🔢 **Confession #:** #{conf_number}\n"
        f"👤 **Requester ID:** `{requester_id}`\n"
        f"⏰ **Time:** {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"**Reason:**\n{reason}"
    )
    
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, deletion_text, parse_mode="Markdown")
        except Exception as e:
            print(f"Failed to send deletion request to admin {admin_id}: {e}")
    
    await callback.message.edit_text(
        f"✅ Deletion request sent for Confession #{conf_number}. "
        "Admins will review your request."
    )
    await state.clear()

@dp.callback_query(F.data == "cancel_deletion")
async def cb_cancel_deletion(callback: types.CallbackQuery, state: FSMContext):
    """Cancels the deletion request process."""
    await callback.answer()
    await callback.message.edit_text("❌ Deletion request cancelled.")
    await state.clear()

@dp.message(DeletionRequestStates.waiting_for_deletion_reason)
async def handle_deletion_reason(msg: types.Message, state: FSMContext):
    """Handles the deletion reason input."""
    await state.update_data(deletion_reason=msg.text)
    
    data = await state.get_data()
    conf_id = data.get('deletion_conf_id')
    
    kb = get_deletion_confirmation_keyboard(conf_id)
    await msg.answer(
        f"📝 **Deletion Reason:**\n{msg.text}\n\n"
        "Please confirm to send this deletion request to admins:",
        reply_markup=kb
    )

# -------------------------
# My Comments System (FIXED - proper comment extraction)
# -------------------------

@dp.message(Command("my_comments"))
async def cmd_my_comments(msg: types.Message, page: int = 1):
    """Shows user's comments with pagination."""
    if msg.chat.type != "private":
        await msg.reply("Please use this command in a private chat with the bot.")
        return
    
    user_id = msg.from_user.id
    page_size = 5
    
    # FIXED: More efficient query to get user's comments
    all_confessions = list(conf_col.find({"comments.user_id": user_id}))
    
    # Extract all comments by this user across all confessions
    user_comments = []
    for confession in all_confessions:
        conf_id = str(confession["_id"])
        conf_number = confession.get("number", "N/A")
        
        for comment_index, comment in enumerate(confession.get("comments", [])):
            if comment.get("user_id") == user_id:
                comment_type = "📝 Text"
                if comment.get('sticker_id'):
                    comment_type = "🎭 Sticker"
                elif comment.get('animation_id'):
                    comment_type = "🎬 GIF"
                
                user_comments.append({
                    "confession_id": conf_id,
                    "confession_number": conf_number,
                    "comment_text": comment.get("text", ""),
                    "comment_type": comment_type,
                    "created_at": comment.get("created_at", datetime.now(UTC)),
                    "comment_index": comment_index
                })
    
    # Sort by creation date (newest first)
    user_comments.sort(key=lambda x: x["created_at"], reverse=True)
    
    # Paginate
    total_comments = len(user_comments)
    total_pages = (total_comments + page_size - 1) // page_size if total_comments > 0 else 1
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_comments = user_comments[start_idx:end_idx]
    
    if not page_comments:
        await msg.answer(
            "💬 **Your Comments**\n\n"
            "You haven't posted any comments yet.\n\n"
            "Browse confessions and engage with the community by commenting!",
            reply_markup=get_my_comments_keyboard([], page, total_pages)
        )
        return
    
    # Format comments list
    comments_text = "💬 **Your Comments**\n\n"
    
    for i, comment_data in enumerate(page_comments):
        conf_number = comment_data["confession_number"]
        comment_type = comment_data["comment_type"]
        comment_text = truncate_text(comment_data["comment_text"], 60) if comment_data["comment_text"] else comment_type
        
        comments_text += f"**On Confession #{conf_number}:**\n"
        comments_text += f'"{comment_text}"\n\n'
    
    comments_text += f"**Page {page}/{total_pages}**"
    
    await msg.answer(
        comments_text,
        reply_markup=get_my_comments_keyboard(page_comments, page, total_pages)
    )

@dp.callback_query(F.data.startswith("my_comments:"))
async def cb_my_comments_page(callback: types.CallbackQuery):
    """Handles my comments pagination."""
    await callback.answer()
    
    try:
        page = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        page = 1
    
    await cmd_my_comments(callback.message, page)

# -------------------------
# Block System Commands
# -------------------------
@dp.message(Command("block"))
async def cmd_block_user(msg: types.Message):
    """Block a user from using the bot."""
    if msg.from_user.id not in ADMIN_IDS:
        await msg.reply("⛔ Admins only.")
        return
    
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.reply("Usage: /block <user_id> [reason]")
        return
    
    try:
        user_id = int(parts[1])
        reason = " ".join(parts[2:]) if len(parts) > 2 else "No reason provided"
    except ValueError:
        await msg.reply("Invalid user ID. Please provide a numeric user ID.")
        return
    
    # Check if user is already blocked
    if user_id in BLOCKED_USERS:
        await msg.reply(f"User {user_id} is already blocked.")
        return
    
    # Add to blocked users
    BLOCKED_USERS.add(user_id)
    blocked_col.update_one(
        {"_id": user_id},
        {"$set": {
            "blocked_by": msg.from_user.id,
            "reason": reason,
            "blocked_at": datetime.now(UTC)
        }},
        upsert=True
    )
    
    # Notify the blocked user (if possible)
    try:
        await bot.send_message(
            user_id,
            "🚫 **You have been blocked from using this bot.**\n\n"
            f"**Reason:** {reason}\n"
            "If you believe this is a mistake, please contact the admins."
        )
    except Exception:
        pass  # User might have blocked the bot or privacy settings prevent messaging
    
    await msg.reply(f"✅ User {user_id} has been blocked from using the bot.")

@dp.message(Command("unblock"))
async def cmd_unblock_user(msg: types.Message):
    """Unblock a user."""
    if msg.from_user.id not in ADMIN_IDS:
        await msg.reply("⛔ Admins only.")
        return
    
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.reply("Usage: /unblock <user_id>")
        return
    
    try:
        user_id = int(parts[1])
    except ValueError:
        await msg.reply("Invalid user ID. Please provide a numeric user ID.")
        return
    
    # Check if user is blocked
    if user_id not in BLOCKED_USERS:
        await msg.reply(f"User {user_id} is not blocked.")
        return
    
    # Remove from blocked users
    BLOCKED_USERS.remove(user_id)
    blocked_col.delete_one({"_id": user_id})
    
    # Notify the unblocked user (if possible)
    try:
        await bot.send_message(
            user_id,
            "✅ **Your access to the bot has been restored.**\n\n"
            "You can now use the bot again. Please follow the rules."
        )
    except Exception:
        pass
    
    await msg.reply(f"✅ User {user_id} has been unblocked.")

@dp.message(Command("blocked_users"))
async def cmd_blocked_users(msg: types.Message):
    """List all blocked users."""
    if msg.from_user.id not in ADMIN_IDS:
        await msg.reply("⛔ Admins only.")
        return
    
    blocked_users = list(blocked_col.find({}))
    
    if not blocked_users:
        await msg.reply("No users are currently blocked.")
        return
    
    blocked_text = "🚫 **Blocked Users**\n\n"
    
    for user in blocked_users:
        user_id = user["_id"]
        reason = user.get("reason", "No reason provided")
        blocked_at = user.get("blocked_at", datetime.now(UTC))
        blocked_by = user.get("blocked_by", "Unknown")
        
        blocked_text += (
            f"👤 **User ID:** `{user_id}`\n"
            f"📝 **Reason:** {reason}\n"
            f"⏰ **Blocked At:** {blocked_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"👮 **Blocked By:** `{blocked_by}`\n"
            f"────────────────────\n"
        )
    
    await msg.reply(blocked_text)

@dp.callback_query(F.data.startswith("admin_block:"))
async def cb_admin_block_from_profile(callback: types.CallbackQuery):
    """Block user from profile view."""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Admin only.", show_alert=True)
        return
    
    try:
        target_user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Invalid user.")
        return
    
    # Block the user
    BLOCKED_USERS.add(target_user_id)
    blocked_col.update_one(
        {"_id": target_user_id},
        {"$set": {
            "blocked_by": callback.from_user.id,
            "reason": "Blocked from profile view",
            "blocked_at": datetime.now(UTC)
        }},
        upsert=True
    )
    
    # Notify the blocked user (if possible)
    try:
        await bot.send_message(
            target_user_id,
            "🚫 **You have been blocked from using this bot.**\n\n"
            "**Reason:** Blocked by admin\n"
            "If you believe this is a mistake, please contact the admins."
        )
    except Exception:
        pass
    
    await callback.answer("User blocked successfully!")
    
    # Refresh the profile view
    await cb_view_profile(callback)

@dp.callback_query(F.data.startswith("admin_unblock:"))
async def cb_admin_unblock_from_profile(callback: types.CallbackQuery):
    """Unblock user from profile view."""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Admin only.", show_alert=True)
        return
    
    try:
        target_user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Invalid user.")
        return
    
    # Unblock the user
    BLOCKED_USERS.remove(target_user_id)
    blocked_col.delete_one({"_id": target_user_id})
    
    # Notify the unblocked user (if possible)
    try:
        await bot.send_message(
            target_user_id,
            "✅ **Your access to the bot has been restored.**\n\n"
            "You can now use the bot again. Please follow the rules."
        )
    except Exception:
        pass
    
    await callback.answer("User unblocked successfully!")
    
    # Refresh the profile view
    await cb_view_profile(callback)

# -------------------------
# Report System
# -------------------------

@dp.callback_query(F.data.startswith("report_user:"))
async def cb_start_report(callback: types.CallbackQuery, state: FSMContext):
    """Starts the user reporting process."""
    await callback.answer()
    
    try:
        target_user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Invalid user.")
        return
    
    await state.update_data(
        report_target_id=target_user_id,
        report_message_id=callback.message.message_id
    )
    
    kb = get_report_confirmation_keyboard(target_user_id)
    await callback.message.answer(
        "🚨 **Report User**\n\n"
        "Please describe why you are reporting this user. Include any relevant details:",
        reply_markup=kb
    )
    await state.set_state(ReportStates.waiting_for_report_reason)

@dp.callback_query(F.data.startswith("confirm_report:"))
async def cb_confirm_report(callback: types.CallbackQuery, state: FSMContext):
    """Confirms and sends the report to admins, with auto-block check."""
    await callback.answer()
    
    try:
        target_user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Invalid user.")
        return
    
    data = await state.get_data()
    reporter_id = callback.from_user.id
    reason = data.get('report_reason', 'No reason provided')
    
    # Store report in database
    reports_col.insert_one({
        "reported_user_id": target_user_id,
        "reporter_id": reporter_id,
        "reason": reason,
        "created_at": datetime.now(UTC)
    })
    
    # Count reports for this user
    report_count = reports_col.count_documents({"reported_user_id": target_user_id})
    
    # Send report to all admins
    report_text = (
        f"🚨 **USER REPORT**\n\n"
        f"👤 **Reported User ID:** `{target_user_id}`\n"
        f"👮 **Reporter ID:** `{reporter_id}`\n"
        f"📊 **Total Reports:** {report_count}/{MAX_REPORTS_BEFORE_BLOCK}\n"
        f"⏰ **Time:** {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"**Reason:**\n{reason}"
    )
    
    # Auto-block if too many reports
    if report_count >= MAX_REPORTS_BEFORE_BLOCK and target_user_id not in BLOCKED_USERS:
        BLOCKED_USERS.add(target_user_id)
        blocked_col.update_one(
            {"_id": target_user_id},
            {"$set": {
                "blocked_by": "AUTO_BLOCK",
                "reason": f"Auto-blocked after {report_count} reports",
                "blocked_at": datetime.now(UTC)
            }},
            upsert=True
        )
        report_text += f"\n\n🚫 **AUTO-BLOCKED** - User has been automatically blocked due to excessive reports."
        
        # Notify the blocked user
        try:
            await bot.send_message(
                target_user_id,
                "🚫 **You have been automatically blocked from using this bot due to multiple reports.**\n\n"
                "If you believe this is a mistake, please contact the admins."
            )
        except Exception:
            pass
    
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, report_text, parse_mode="Markdown")
        except Exception as e:
            print(f"Failed to send report to admin {admin_id}: {e}")
    
    await callback.message.edit_text("✅ User reported successfully. Admins will review the report.")
    await state.clear()

@dp.callback_query(F.data == "cancel_report")
async def cb_cancel_report(callback: types.CallbackQuery, state: FSMContext):
    """Cancels the reporting process."""
    await callback.answer()
    await callback.message.edit_text("❌ Report cancelled.")
    await state.clear()

@dp.message(ReportStates.waiting_for_report_reason)
async def handle_report_reason(msg: types.Message, state: FSMContext):
    """Handles the report reason input."""
    await state.update_data(report_reason=msg.text)
    
    data = await state.get_data()
    target_user_id = data.get('report_target_id')
    
    kb = get_report_confirmation_keyboard(target_user_id)
    await msg.answer(
        f"📝 **Report Reason:**\n{msg.text}\n\n"
        "Please confirm to send this report to admins:",
        reply_markup=kb
    )

# -------------------------
# Chat Request System
# -------------------------

@dp.callback_query(F.data.startswith("request_chat:"))
async def cb_start_chat_request(callback: types.CallbackQuery, state: FSMContext):
    """Starts the chat request process."""
    await callback.answer()
    
    try:
        target_user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Invalid user.")
        return
    
    await state.update_data(
        chat_target_id=target_user_id,
        chat_request_message_id=callback.message.message_id
    )
    
    kb = get_chat_request_confirmation_keyboard(target_user_id)
    await callback.message.answer(
        "💬 **Request Chat**\n\n"
        "Send a message to introduce yourself to this user. "
        "If they accept, your username will be shared with them:",
        reply_markup=kb
    )
    await state.set_state(ChatRequestStates.waiting_for_chat_request_message)

@dp.callback_query(F.data.startswith("send_chat_request:"))
async def cb_send_chat_request(callback: types.CallbackQuery, state: FSMContext):
    """Sends the chat request to the target user."""
    await callback.answer()
    
    try:
        target_user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Invalid user.")
        return
    
    data = await state.get_data()
    requester_id = callback.from_user.id
    request_message = data.get('chat_request_message', 'No message provided')
    
    # Generate unique request ID
    request_id = str(ObjectId())
    
    # Store chat request in database
    chat_request = {
        "_id": request_id,
        "requester_id": requester_id,
        "target_user_id": target_user_id,
        "message": request_message,
        "status": "pending",
        "created_at": datetime.now(UTC)
    }
    
    # Save to database (you might want to create a separate collection for this)
    # For now, we'll store it in users_col for simplicity
    users_col.update_one(
        {"_id": target_user_id},
        {"$push": {"pending_chat_requests": chat_request}}
    )
    
    # Send notification to target user
    requester_profile = get_user_profile(requester_id)
    requester_name = requester_profile.get("nickname", "Anonymous")
    requester_emoji = requester_profile.get("emoji", "👤")
    
    request_text = (
        f"💌 **Chat Request**\n\n"
        f"{requester_emoji} **{requester_name}** wants to chat with you!\n\n"
        f"**Their message:**\n{request_message}\n\n"
        f"Do you want to share your username with them?"
    )
    
    kb = get_chat_request_response_keyboard(request_id, requester_id)
    
    try:
        await bot.send_message(target_user_id, request_text, reply_markup=kb, parse_mode="Markdown")
        await callback.message.edit_text("✅ Chat request sent! The user will be notified.")
    except TelegramForbiddenError:
        await callback.message.edit_text("❌ Cannot send chat request. The user may have blocked the bot.")
    except Exception as e:
        await callback.message.edit_text("❌ Failed to send chat request.")
        print(f"Chat request error: {e}")
    
    await state.clear()

@dp.callback_query(F.data.startswith("accept_chat_request:"))
async def cb_accept_chat_request(callback: types.CallbackQuery):
    """Accepts a chat request and shares usernames."""
    await callback.answer()
    
    try:
        _, request_id, requester_id = callback.data.split(":")
        requester_id = int(requester_id)
    except (ValueError, IndexError):
        await callback.answer("Invalid request.")
        return
    
    target_user_id = callback.from_user.id
    
    # Get usernames
    try:
        requester_chat = await bot.get_chat(requester_id)
        target_chat = await bot.get_chat(target_user_id)
        
        requester_username = f"@{requester_chat.username}" if requester_chat.username else "No username"
        target_username = f"@{target_chat.username}" if target_chat.username else "No username"
        
        # Notify requester
        await bot.send_message(
            requester_id,
            f"✅ Your chat request was accepted!\n\n"
            f"👤 **User's username:** {target_username}\n\n"
            f"You can now start a conversation with them."
        )
        
        # Notify target user
        await callback.message.edit_text(
            f"✅ You accepted the chat request!\n\n"
            f"👤 **Requester's username:** {requester_username}\n\n"
            f"You can now start a conversation with them."
        )
        
    except Exception as e:
        await callback.message.edit_text("❌ Error processing chat request.")
        print(f"Chat request acceptance error: {e}")

@dp.callback_query(F.data.startswith("decline_chat_request:"))
async def cb_decline_chat_request(callback: types.CallbackQuery):
    """Declines a chat request."""
    await callback.answer()
    
    try:
        request_id = callback.data.split(":")[1]
    except (ValueError, IndexError):
        await callback.answer("Invalid request.")
        return
    
    await callback.message.edit_text("❌ Chat request declined.")

@dp.callback_query(F.data == "cancel_chat_request")
async def cb_cancel_chat_request(callback: types.CallbackQuery, state: FSMContext):
    """Cancels the chat request process."""
    await callback.answer()
    await callback.message.edit_text("❌ Chat request cancelled.")
    await state.clear()

@dp.message(ChatRequestStates.waiting_for_chat_request_message)
async def handle_chat_request_message(msg: types.Message, state: FSMContext):
    """Handles the chat request message input."""
    await state.update_data(chat_request_message=msg.text)
    
    data = await state.get_data()
    target_user_id = data.get('chat_target_id')
    
    kb = get_chat_request_confirmation_keyboard(target_user_id)
    await msg.answer(
        f"📝 **Your message:**\n{msg.text}\n\n"
        "Send this chat request to the user?",
        reply_markup=kb
    )

# -------------------------
# Privacy Settings System
# -------------------------

@dp.callback_query(F.data == "privacy_settings")
async def cb_privacy_settings(callback: types.CallbackQuery):
    """Shows privacy settings menu."""
    await callback.answer()
    
    user_id = callback.from_user.id
    profile = get_user_profile(user_id)
    
    kb = get_privacy_settings_keyboard(profile)
    await callback.message.edit_text(
        "🔒 **Privacy Settings**\n\n"
        "Choose what information is visible to others:",
        reply_markup=kb
    )

@dp.callback_query(F.data == "toggle_bio_privacy")
async def cb_toggle_bio_privacy(callback: types.CallbackQuery):
    """Toggles bio visibility."""
    await callback.answer()
    
    user_id = callback.from_user.id
    profile = get_user_profile(user_id)
    privacy = profile.get("privacy_settings", {})
    
    # Toggle bio visibility
    privacy["bio_visible"] = not privacy.get("bio_visible", False)
    
    users_col.update_one(
        {"_id": user_id},
        {"$set": {"privacy_settings": privacy, "updated_at": datetime.now(UTC)}}
    )
    
    # Refresh the privacy settings menu
    profile = get_user_profile(user_id)
    kb = get_privacy_settings_keyboard(profile)
    
    await callback.message.edit_text(
        "🔒 **Privacy Settings**\n\n"
        "Choose what information is visible to others:",
        reply_markup=kb
    )

@dp.callback_query(F.data == "toggle_gender_privacy")
async def cb_toggle_gender_privacy(callback: types.CallbackQuery):
    """Toggles gender visibility."""
    await callback.answer()
    
    user_id = callback.from_user.id
    profile = get_user_profile(user_id)
    privacy = profile.get("privacy_settings", {})
    
    # Toggle gender visibility
    privacy["gender_visible"] = not privacy.get("gender_visible", False)
    
    users_col.update_one(
        {"_id": user_id},
        {"$set": {"privacy_settings": privacy, "updated_at": datetime.now(UTC)}}
    )
    
    # Refresh the privacy settings menu
    profile = get_user_profile(user_id)
    kb = get_privacy_settings_keyboard(profile)
    
    await callback.message.edit_text(
        "🔒 **Privacy Settings**\n\n"
        "Choose what information is visible to others:",
        reply_markup=kb
    )

@dp.callback_query(F.data == "set_gender")
async def cb_set_gender_start(callback: types.CallbackQuery):
    """Starts gender selection."""
    await callback.answer()
    
    kb = get_gender_selection_keyboard()
    await callback.message.edit_text(
        "⚧️ **Set Your Gender**\n\n"
        "Choose your gender (this can be set to private in privacy settings):",
        reply_markup=kb
    )

@dp.callback_query(F.data.startswith("gender_"))
async def cb_handle_gender_selection(callback: types.CallbackQuery):
    """Handles gender selection."""
    await callback.answer()
    
    gender_map = {
        "gender_male": "Male",
        "gender_female": "Female", 
        "gender_aka47": "AKA47",
        "gender_not_say": "Prefer not to say"
    }
    
    gender = gender_map.get(callback.data, "Not specified")
    user_id = callback.from_user.id
    
    users_col.update_one(
        {"_id": user_id},
        {"$set": {"gender": gender, "updated_at": datetime.now(UTC)}}
    )
    
    await callback.message.edit_text(f"✅ Gender set to: {gender}")

# -------------------------
# /confess flow (private only) - STEP 1: Text/Media
# -------------------------
@dp.message(Command("confess"))
async def cmd_confess_start(msg: types.Message, state: FSMContext):
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
            await msg.answer(f"✅ Submitted and **AUTO-APPROVED**! Your confession is now live as **Confession #{final_doc['number']}**.", reply_markup=get_main_reply_keyboard())
        else:
            await msg.answer("⚠️ Submitted, but failed to post to the channel. Admins have been notified.", reply_markup=get_main_reply_keyboard())
    # Manual Approval Path
    else:
        await msg.answer("✅ Submitted! Admins will review and approve if it follows the rules. You will be notified privately when it's approved or rejected.", reply_markup=get_main_reply_keyboard())
        
        # Send to admins for manual review
        kb = admin_kb(str(res.inserted_id))
        
        # Truncate text for admin caption if media is present (Limit 1024 characters)
        admin_text = text
        if media:
            admin_text = truncate_text(admin_text, 1000)

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
# Helper: publish approved confession (UPDATED with comment count - FIXED)
# -------------------------
def next_conf_number():
    """Get next confession number, handles reset scenarios properly"""
    # Find the highest current number among approved confessions
    last_confession = conf_col.find_one(
        {"approved": True, "number": {"$ne": None}},
        sort=[("number", -1)]
    )
    
    if last_confession and last_confession.get("number"):
        return last_confession["number"] + 1
    else:
        # If no numbered confessions exist, start from 1
        # Don't count approved confessions as they might have null numbers after reset
        return 1

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
    
    # FIXED: Get actual comment count for the button text
    comment_count = len(doc.get("comments", []))
    
    reaction_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"👍 {likes}", callback_data=f"vote:like:{conf_id}"),
            InlineKeyboardButton(text=f"👎 {dislikes}", callback_data=f"vote:dislike:{conf_id}")
        ],
        [
            # FIXED: Updated button text with actual comment count
            InlineKeyboardButton(text=f"💬 View / Add Comment ({comment_count})", url=bot_url) 
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
async def send_notification(user_id, message_text, reply_markup=None):
    """Sends a private notification, handling potential blocks."""
    try:
        await bot.send_message(user_id, message_text, reply_markup=reply_markup, parse_mode="Markdown", disable_web_page_preview=True)
        return True
    except TelegramForbiddenError:
        print(f"User {user_id} blocked the bot.")
        return False
    except Exception as e:
        print(f"Error sending notification to {user_id}: {e}")
        return False

# -------------------------
# COMMENT/REPLY FLOW START (Callback) (UPDATED - FIXED)
# -------------------------
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
            "You can send text, GIFs, or stickers as comments."
        )
    else:
        comments = doc.get("comments", [])
        if parent_index >= len(comments):
             await callback.message.answer("Error: Cannot find parent comment. Starting new top-level comment instead.")
             await state.update_data(parent_index=-1)
             return
             
        # Get the parent comment for context
        parent_comment = comments[parent_index]
        parent_profile = get_user_profile(parent_comment.get('user_id'))
        parent_nickname = parent_profile.get("nickname", "Anonymous")
        
        # Display the comment we are replying to (without the "(to Anon X)" part)
        parent_text_preview = truncate_text(parent_comment.get('text', '...'), 50)
        
        await callback.message.answer(
            f"↩️ **Replying to {parent_nickname}**\n"
            f"> {parent_text_preview}\n\n"
            "Please type your reply now. It will be posted anonymously."
        )


# -------------------------
# COMMENT/REPLY FLOW SUBMIT (Receiving the comment/reply text) (UPDATED - Now supports unlimited nesting)
# -------------------------
@dp.message(CommentStates.waiting_for_submission)
async def handle_comment_submission(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    conf_id = data.get("target_conf_id")
    # parent_index is the 0-based index of the comment being replied to. -1 means top-level.
    parent_index = data.get("parent_index", -1) 
    
    if not conf_id:
        await msg.answer("❌ Submission failed. Please click 'Add Comment'/'Reply' again.")
        await state.clear()
        return

    # Check if message is empty
    if not msg.text and not msg.sticker and not msg.animation:
        await msg.answer("Please send a valid comment (text, GIF, or sticker).")
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
        
        if parent_index != -1:
            comments = doc.get("comments", [])
            if parent_index < len(comments):  # FIX: Check if parent_index is valid
                parent_comment = comments[parent_index]
                parent_author_id = parent_comment["user_id"]
                
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
        
        # 2. Build the new comment object with support for different media types
        new_comment = {
            "user_id": msg.from_user.id, 
            "created_at": datetime.now(UTC),
            "likes": 0, 
            "dislikes": 0, 
            "comment_voters": {},
            # REMOVED: "replying_to_anon" field since we're using Telegram's native reply feature
            # CRITICAL: Store the 0-based index of the parent comment
            "parent_index": parent_index 
        }
        
        # Handle different content types
        if msg.sticker:
            new_comment["sticker_id"] = msg.sticker.file_id
            new_comment["sticker_emoji"] = msg.sticker.emoji or "🎭"
        elif msg.animation:  # GIFs
            new_comment["animation_id"] = msg.animation.file_id
        elif msg.text:
            # FIXED: Ensure text comments are properly stored
            if len(msg.text) > 4000:
                await msg.answer("Your comment is too long (max 4000 characters). Please shorten it.")
                return
            new_comment["text"] = msg.text.strip()  # Ensure text is properly stored
        else:
            await msg.answer("Unsupported media type. Please use text, GIFs, or stickers only.")
            return

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
# FIXED: Comment Voting Logic (UPDATED - In-place updates with notifications and better error handling)
# -------------------------
@dp.callback_query(F.data.startswith("cmt_vote:"))
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
    
    # FIXED: Better self-vote error message
    if comment.get("user_id") == user_id:
        await callback.answer("❌ You cannot vote on your own comment.", show_alert=True)
        return
        
    voters = comment.get("comment_voters", {})
    current_vote = voters.get(str_user_id, 0)  # FIXED: Added default value 0
    
    vote_value = 1 if vote_type == "like" else -1
    
    new_likes = comment.get("likes", 0)
    new_dislikes = comment.get("dislikes", 0)
    karma_change = 0
    
    # Track if this is a new vote (for notification)
    is_new_vote = False
    
    if current_vote == 0:
        if vote_type == "like":
            new_likes += 1
        else:
            new_dislikes += 1
        voters[str_user_id] = vote_value
        karma_change = vote_value
        is_new_vote = True
        
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
        is_new_vote = True
    
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
    
    # 3. Send notification for new votes (only if it's a new vote or vote change)
    if is_new_vote and comment_author_id != user_id:
        await send_vote_notification(comment_author_id, doc.get("number", "N/A"), vote_type, is_comment=True, conf_id=conf_id)

    # 4. Update the comment message in-place instead of sending new messages
    try:
        updated_doc = conf_col.find_one({"_id": ObjectId(conf_id)})
        updated_comment = updated_doc["comments"][comment_index]
        
        updated_kb = get_comment_keyboard(
            conf_id, updated_comment, user_id, comment_author_id, new_likes, new_dislikes
        )
        
        # FIXED: Check if markup actually changed before editing
        current_markup = callback.message.reply_markup
        if str(current_markup) != str(updated_kb):
            await callback.message.edit_reply_markup(reply_markup=updated_kb)
        else:
            # If no change, just show a brief feedback
            await callback.answer(f"Vote recorded!", show_alert=False)
        
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            # Ignore "not modified" errors - this is normal when voting multiple times quickly
            await callback.answer(f"Vote recorded!", show_alert=False)
        else:
            print(f"Error updating comment vote UI: {e}")
            # Don't fall back to full refresh - just show feedback
            await callback.answer(f"Vote recorded! Karma change: {karma_change}")
    except Exception as e:
        print(f"Error updating comment vote UI: {e}")
        # Don't fall back to full refresh - just show feedback
        await callback.answer(f"Vote recorded! Karma change: {karma_change}")


# -------------------------
# Karma System: Handle Post Votes (Like/Dislike) (UPDATED with comment count - FIXED)
# -------------------------
@dp.callback_query(F.data.startswith("vote:"))
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

    # FIXED: Better self-vote error message
    if doc.get("user_id") == user_id:
        await callback.answer("❌ You cannot vote on your own confession.", show_alert=True)
        return
        
    voters = doc.get("voters", {})
    current_vote = voters.get(str_user_id, 0) 
    
    vote_value = 1 if vote_type == "like" else -1
    
    new_likes = doc.get("likes", 0)
    new_dislikes = doc.get("dislikes", 0)
    karma_change = 0
    
    # Track if this is a new vote (for notification)
    is_new_vote = False
    
    if current_vote == 0:
        if vote_type == "like":
            new_likes += 1
        else:
            new_dislikes += 1
        voters[str_user_id] = vote_value
        karma_change = vote_value
        is_new_vote = True
        
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
        is_new_vote = True

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
    
    # 3. Send notification for new votes (only if it's a new vote or vote change)
    if is_new_vote and confessor_id != user_id:
        await send_vote_notification(confessor_id, doc.get("number", "N/A"), vote_type, is_comment=False, conf_id=conf_id)

    # 4. Update the Reaction Keyboard on the Channel Message
    # CRITICAL: Rebuild the keyboard with the correct deep-link
    bot_url = f"https://t.me/{BOT_USERNAME}?start=comment_{conf_id}" 
    
    # FIXED: Get updated comment count for the button
    updated_doc = conf_col.find_one({"_id": ObjectId(conf_id)})
    comment_count = len(updated_doc.get("comments", []))
    
    reaction_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"👍 {new_likes}", callback_data=f"vote:like:{conf_id}"),
            InlineKeyboardButton(text=f"👎 {new_dislikes}", callback_data=f"vote:dislike:{conf_id}")
        ],
        [
            # FIXED: Re-use the new comment button logic with updated comment count
            InlineKeyboardButton(text=f"💬 View / Add Comment ({comment_count})", url=bot_url)
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
        
    # 5. Don't refresh the private chat view - just show feedback
    await callback.answer(f"Vote recorded! Karma change: {karma_change}")


# -------------------------
# NEW: Leaderboard Command
# -------------------------
@dp.message(Command("leaderboard"))
async def cmd_leaderboard(msg: types.Message):
    """Shows the top users by aura points."""
    # Get top 10 users by karma
    top_users = list(karma_col.find().sort("karma", -1).limit(10))
    
    if not top_users:
        await msg.answer("No users with aura points yet. Be the first to get some by posting confessions and comments!")
        return
    
    leaderboard_text = "🏆 **Aura Leaderboard** 🏆\n\n"
    
    for i, user_data in enumerate(top_users):
        user_id = user_data["_id"]
        karma_score = user_data.get("karma", 0)
        
        # Get user profile for nickname and emoji
        profile = get_user_profile(user_id)
        nickname = profile.get("nickname", "Anonymous")
        emoji = profile.get("emoji", "👤")
        
        # Medal emojis for top 3
        medal = ""
        if i == 0:
            medal = "🥇"
        elif i == 1:
            medal = "🥈"
        elif i == 2:
            medal = "🥉"
        else:
            medal = f"**{i+1}.**"
        
        leaderboard_text += f"{medal} {emoji} **{nickname}** - ⚡{karma_score} Aura\n"
    
    leaderboard_text += "\nEarn aura points by getting likes on your confessions and comments!"
    
    await msg.answer(leaderboard_text, parse_mode="Markdown")


# -------------------------
# Admin: build admin keyboard for a confession (No change)
# -------------------------
def admin_kb(conf_id: str):
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
    
    user_id = callback.from_user.id
    profile = get_user_profile(user_id)
    kb = get_edit_profile_keyboard(profile)
    
    try:
        await callback.message.edit_text(
            "⚙️ **Edit Your Profile**\n\nChoose an option below:",
            reply_markup=kb, 
            parse_mode="Markdown"
        )
    except TelegramAPIError:
        pass

# --------------------
# 1. Edit Nickname Flow (UPDATED with 30-day cooldown - FIXED TIMEZONE ISSUE)
# --------------------
@dp.callback_query(F.data == "edit_nickname")
async def cb_edit_nickname_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    
    user_id = callback.from_user.id
    profile = get_user_profile(user_id)
    
    # Check nickname change cooldown - FIXED TIMEZONE ISSUE
    last_change = profile.get("last_nickname_change")
    if last_change:
        # Ensure both datetimes are timezone-aware
        current_time = datetime.now(UTC)
        if last_change.tzinfo is None:
            # If last_change is naive, make it aware
            last_change = last_change.replace(tzinfo=UTC)
        
        time_since_change = (current_time - last_change).total_seconds()
        if time_since_change < NICKNAME_CHANGE_COOLDOWN:
            days_left = int((NICKNAME_CHANGE_COOLDOWN - time_since_change) / (24 * 60 * 60)) + 1
            await callback.message.answer(f"⏳ You can change your nickname again in {days_left} days.")
            return
    
    await state.set_state(ProfileStates.editing_nickname)
    await callback.message.edit_text(
        f"⭐ **Enter your new anonymous Nickname.**\n"
        f"Min {MIN_NICKNAME_LENGTH}, Max {MAX_NICKNAME_LENGTH} characters. Use letters and numbers only.\n\n"
        f"⚠️ **Note:** You can only change your nickname once every 30 days."
    )

@dp.message(ProfileStates.editing_nickname)
async def handle_new_nickname(msg: types.Message, state: FSMContext):
    new_nickname = msg.text.strip()
    user_id = msg.from_user.id
    
    if not new_nickname or len(new_nickname) < MIN_NICKNAME_LENGTH:
        await msg.answer(f"Your nickname is too short (min {MIN_NICKNAME_LENGTH} characters). Please try again.")
        return
    
    if len(new_nickname) > MAX_NICKNAME_LENGTH:
        await msg.answer(f"Your nickname is too long (max {MAX_NICKNAME_LENGTH} characters). Please try again.")
        return

    # Optional: Basic validation to disallow common profanities or restricted names
    if re.search(r'[^a-zA-Z0-9\s]', new_nickname):
        await msg.answer("Nickname can only contain letters, numbers, and spaces. Please try again.")
        return

    try:
        users_col.update_one(
            {"_id": user_id},
            {"$set": {
                "nickname": new_nickname, 
                "updated_at": datetime.now(UTC),
                "last_nickname_change": datetime.now(UTC)
            }},
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
# Profile View Callback Handler
# -------------------------
@dp.callback_query(F.data.startswith("view_profile:"))
async def cb_view_profile(callback: types.CallbackQuery):
    """Handles viewing other users' profiles."""
    await callback.answer()
    
    try:
        target_user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Invalid user.")
        return
    
    viewer_id = callback.from_user.id
    
    # Get target user's profile
    profile = get_user_profile(target_user_id)
    karma_doc = karma_col.find_one({"_id": target_user_id}) or {}
    karma_score = karma_doc.get('karma', 0)
    
    # Format public profile
    profile_text = format_public_profile_message(profile, karma_score)
    kb = get_user_profile_keyboard(target_user_id, viewer_id)
    
    await callback.message.edit_text(profile_text, reply_markup=kb, parse_mode="Markdown")

# -------------------------
# NEW: User Question/Feedback System
# -------------------------
@dp.message(Command("ask"))
async def cmd_ask_question(msg: types.Message, state: FSMContext):
    """Starts the process for users to ask questions/feedback to admins."""
    if msg.chat.type != "private":
        await msg.reply("Please use this command in a private chat with the bot.")
        return
    
    await msg.answer(
        "💬 **Ask a Question / Send Feedback**\n\n"
        "Please type your question or feedback for the admins. "
        "They will reply to you anonymously through the bot."
    )
    await state.set_state(UserQuestionStates.waiting_for_question)

@dp.message(UserQuestionStates.waiting_for_question)
async def handle_user_question(msg: types.Message, state: FSMContext):
    """Handles user question and forwards it to admins."""
    question_text = msg.text
    user_id = msg.from_user.id
    
    if not question_text or len(question_text.strip()) < 5:
        await msg.answer("Your question is too short. Please send a question of at least 5 characters.")
        return
    
    if len(question_text) > 2000:
        await msg.answer("Your question is too long. Please keep it under 2000 characters.")
        return
    
    # Generate unique question ID
    question_id = str(ObjectId())
    
    # Store question in database
    question_data = {
        "_id": question_id,
        "user_id": user_id,
        "question": question_text,
        "created_at": datetime.now(UTC),
        "status": "pending",
        "admin_replies": []
    }
    
    # Store in users collection
    users_col.update_one(
        {"_id": user_id},
        {"$push": {"user_questions": question_data}},
        upsert=True
    )
    
    # Send question to all admins
    question_text_display = (
        f"❓ **USER QUESTION**\n\n"
        f"👤 **User ID:** `{user_id}`\n"
        f"🆔 **Question ID:** `{question_id}`\n"
        f"⏰ **Time:** {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"**Question:**\n{question_text}"
    )
    
    # Create keyboard for admins to reply
    reply_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Reply Anonymously", callback_data=f"admin_reply:{question_id}:{user_id}")]
    ])
    
    sent_to_admins = False
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, question_text_display, reply_markup=reply_kb, parse_mode="Markdown")
            sent_to_admins = True
        except Exception as e:
            print(f"Failed to send question to admin {admin_id}: {e}")
    
    if sent_to_admins:
        await msg.answer(
            "✅ Your question has been sent to the admins! "
            "They will reply to you anonymously through this bot."
        )
    else:
        await msg.answer(
            "❌ Sorry, we couldn't send your question to the admins at this time. "
            "Please try again later."
        )
    
    await state.clear()

@dp.callback_query(F.data.startswith("admin_reply:"))
async def cb_admin_reply_start(callback: types.CallbackQuery, state: FSMContext):
    """Starts the admin reply process for a user question."""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Admin only.", show_alert=True)
        return
    
    try:
        _, question_id, user_id = callback.data.split(":")
        user_id = int(user_id)
    except (ValueError, IndexError):
        await callback.answer("Invalid question data.")
        return
    
    await state.update_data(
        question_id=question_id,
        target_user_id=user_id
    )
    
    await callback.message.answer(
        f"✉️ **Reply to User Question**\n\n"
        f"Question ID: `{question_id}`\n"
        f"User ID: `{user_id}`\n\n"
        "Please type your anonymous reply to this user:"
    )
    await state.set_state(ReplyStates.waiting_for_reply)
    await callback.answer()
    # -------------------------
# NEW: Reset Numbering Command (Keeps all data)
# -------------------------
@dp.message(Command("reset_numbering"))
async def cmd_reset_numbering(msg: types.Message):
    """Reset confession numbering while keeping all data (Admin only)"""
    if msg.from_user.id not in ADMIN_IDS:
        await msg.reply("⛔ Admins only.")
        return
    
    # Reset all confession numbers to null
    result = conf_col.update_many(
        {}, 
        {"$set": {"number": None}}
    )
    
    await msg.answer(
        f"✅ Numbering reset! {result.modified_count} confessions now have null numbers.\n"
        f"Next approved confession will start from #1."
    )

# -------------------------
# Other Commands (UPDATED with new menu structure)
# -------------------------
@dp.message(Command("help"))
async def cmd_help(msg: types.Message):
    is_admin = msg.from_user.id in ADMIN_IDS
    admin_commands = (
        "\n\n**Admin Commands:**\n"
        "/pending - list pending confessions\n"
        "/toggle_auto_approve - switch between manual/auto approval\n"
        "/block <user_id> - block a user from using the bot\n"
        "/unblock <user_id> - unblock a user\n"
        "/blocked_users - list all blocked users\n"
    ) if is_admin else ""
    
    await msg.answer(
        "🤖 **Confession Bot Commands**\n\n"
        "**Main Commands:**\n"
        "/confess - submit anonymous confession\n"
        "/profile - manage your bio and view karma\n"
        "/menu - show all available options\n\n"
        "**Additional Commands:**\n"
        "/my_confessions - view your submitted confessions\n"
        "/my_comments - view your comments\n"
        "/leaderboard - view top users by aura points\n"
        "/ask - ask a question or send feedback to admins\n"
        "/rules - channel rules\n"
        "/latest - show latest approved confessions\n"
        "/random - show a random approved confession\n"
        "/find <number> - find confession by number\n"
        "/ping - test if bot is responding\n"
        "/test - test basic functionality"
        f"{admin_commands}"
    )

@dp.message(Command("rules"))
async def cmd_rules(msg: types.Message):
    rules_text = (
        "📜 Bot Rules & Regulations\n\n"
        "To keep the community safe, respectful, and meaningful, please follow these guidelines when using the bot:\n\n"
        "1.  **Stay Relevant:** This space is mainly for sharing confessions, experiences, and thoughts.\n"
        "    - Avoid using it just to ask random questions you could easily Google or ask in the right place.\n"
        "    - Some Academic-related questions may be approved if they benefit the community.\n\n"
        "2.  **Respectful Communication:** Sensitive topics (political, religious, cultural, etc.) are allowed but must be discussed with respect.\n\n"
        "3.  **No Harmful Content:** You may mention names, but at your own risk.\n"
        "    - The bot and admins are not responsible for any consequences.\n"
        "    - If someone mentioned requests removal, their name will be taken down.\n\n"
        "4.  **Names & Responsibility:** Do not share personal identifying information about yourself or others.\n\n"
        "5.  **Anonymity & Privacy:** Don't reveal private details of others (contacts, address, etc.) without consent.\n\n"
        "6.  **Constructive Environment:** Keep confessions genuine. Avoid spam, trolling, or repeated submissions.\n"
        "    - Respect moderators' decisions on approvals, edits, or removals.\n\n"
        "Use this space to connect, share, and learn, not to spread misinformation or cause unnecessary drama."
    )
    await msg.answer(rules_text)

@dp.message(Command("menu"))
async def cmd_menu(msg: types.Message):
    """Shows the main menu."""
    await show_more_menu(msg)

@dp.message(Command("my_karma"))
async def cmd_my_karma(msg: types.Message):
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

@dp.callback_query(lambda c: c.data and not c.data.startswith('vote:') and not c.data.startswith('comment_start:') and not c.data.startswith('cmt_vote:') and c.data != "profile_edit_bio" and not c.data.startswith("set_emoji:") and c.data not in ["profile_view", "profile_edit", "edit_nickname", "edit_bio", "change_emoji", "privacy_settings", "toggle_bio_privacy", "toggle_gender_privacy", "set_gender"] and not c.data.startswith("gender_") and not c.data.startswith("view_profile:") and not c.data.startswith("report_user:") and not c.data.startswith("request_chat:") and not c.data.startswith("confirm_report:") and not c.data.startswith("send_chat_request:") and not c.data.startswith("accept_chat_request:") and not c.data.startswith("decline_chat_request:") and not c.data.startswith("admin_reply:") and not c.data.startswith("my_confessions:") and not c.data.startswith("my_comments:") and not c.data.startswith("request_deletion:") and not c.data.startswith("confirm_deletion:"))
async def cb_admin_actions(callback: types.CallbackQuery, state: FSMContext):
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
            # FIXED: Properly update the message instead of leaving it hanging
            try:
                await callback.message.edit_text("⚠️ This confession was already processed (approved or rejected).")
            except:
                pass
            return
            
        tags_text = ' '.join([f'#{t.replace(" ", "_")}' for t in doc.get("tags", [])])
        
        final_doc, success = await publish_confession(doc, tags_text)
        
        if success:
            # FIXED: Properly update admin approval message
            try:
                await callback.message.edit_text(f"✅ Approved & posted as Confession #{final_doc['number']}.")
            except:
                await callback.message.answer(f"✅ Approved & posted as Confession #{final_doc['number']}.")
        else:
            # FIXED: Properly update admin approval message
            try:
                await callback.message.edit_text("⚠️ Approved, but **failed to post** to the channel after multiple retries. The bot has sent you a separate **CRITICAL ERROR** notification.")
            except:
                await callback.message.answer("⚠️ Approved, but **failed to post** to the channel after multiple retries. The bot has sent you a separate **CRITICAL ERROR** notification.")
        
        await callback.answer("Approval process completed.")

    elif action == "no":
        if not doc or doc.get("approved"):
            await callback.answer("Not found or already processed.")
            return
        
        conf_col.delete_one({"_id": ObjectId(conf_id)})
        # FIXED: Properly update rejection message
        try:
            await callback.message.edit_text("❌ Rejected & deleted.")
        except:
            await callback.message.answer("❌ Rejected & deleted.")
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
    docs = list(conf_col.find({"approved": True}).sort("approved_at", -1).limit(5))
    if not docs:
        await msg.answer("No approved confessions yet.")
        return
        
    await msg.answer(f"🔎 Showing latest {len(docs)} confessions. Use the /start comment_<id> link on any post in the channel to see its full view.")


@dp.message(Command("random"))
async def cmd_random(msg: types.Message):
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
import asyncio

# ... your other imports and code ...

async def main():
    print("🚀 Starting bot with debug information...")
    
    # Test MongoDB
    try:
        client.admin.command('ping')
        print("✅ MongoDB: Connected")
    except Exception as e:
        print(f"❌ MongoDB: {e}")
        return
    
    # Test Bot
    try:
        me = await bot.get_me()
        print(f"✅ Bot: Connected as @{me.username}")
    except Exception as e:
        print(f"❌ Bot: {e}")
        return
    
    # Load settings
    load_blocked_users()
    print(f"✅ Loaded {len(BLOCKED_USERS)} blocked users")
    print(f"✅ Auto-approve: {GLOBAL_AUTO_APPROVE}")
    print(f"✅ Admin IDs: {ADMIN_IDS}")
    print(f"✅ Group ID: {GROUP_ID}")
    print(f"✅ Channel ID: {CHANNEL_ID}")
    
    print("📡 Starting web server first (Render requirement)...")
    
    # Start web server FIRST - this is critical for Render
    await start_web_server()
    print("✅ Web server started on port 10000")
    
    print("🤖 Starting bot polling...")
    
    # Then start bot polling
    await dp.start_polling(bot, drop_pending_updates=True)

# Update the if __name__ block at the very bottom of your file:
if __name__ == "__main__":
    asyncio.run(main())





