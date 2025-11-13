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
ADMIN_IDS = [905781541,7001310702,6347817894] 
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

    return (
        f"{emoji} **{nickname}'s Profile**\n"
        f"🆔 User ID: `{user_id}`\n\n"
        f"✨ **Aura:** `{aura}` points (from post/comment voting)\n"
        f"⚧️ **Gender:** {gender} ({gender_visibility})\n\n"
        f"📝 **Bio:**\n"
        f"_{bio}_\n"
        f"({bio_visibility})\n\n"
        f"Use /leaderboard to see the top Aura holders!"
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
        f"✨ **Aura:** `{aura}` points\n"
        f"{gender_text}"
        f"{bio_text}"
    )

# ------------------------
# Keyboard Builders (UPDATED)
# ------------------------

def get_profile_menu_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for the main profile view."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Edit Profile", callback_data="profile_edit")
    builder.adjust(1)
    return builder.as_markup()

def get_edit_profile_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for the profile editing menu."""
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐ Edit Nickname", callback_data="edit_nickname")
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
    builder.button(text="⚧️ Other", callback_data="gender_other")
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
    """Keyboard for viewing another user's profile."""
    builder = InlineKeyboardBuilder()
    
    # Only show report and chat request if not viewing own profile
    if target_user_id != viewer_user_id:
        builder.button(text="🚨 Report User", callback_data=f"report_user:{target_user_id}")
        builder.button(text="💬 Request Chat", callback_data=f"request_chat:{target_user_id}")
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

# -------------------------
# Comment Display Functions (COMPLETELY REDESIGNED)
# -------------------------

def organize_comments_into_threads(comments):
    """Organizes comments into threaded structure."""
    threads = []
    
    # Add index to each comment for reference
    for i, comment in enumerate(comments):
        comment['_index'] = i
    
    # Find top-level comments (parent_index = -1)
    top_level_comments = [c for c in comments if c.get('parent_index', -1) == -1]
    
    for top_comment in top_level_comments:
        thread = {
            'main': top_comment,
            'replies': []
        }
        
        # Find replies to this top-level comment
        top_index = comments.index(top_comment)
        replies = [c for c in comments if c.get('parent_index', -1) == top_index]
        thread['replies'] = replies
        
        threads.append(thread)
    
    return threads

async def send_comment_thread(msg: types.Message, main_message_id: int, thread: dict, conf_id: str, viewer_id: int):
    """Sends a comment thread with proper threading."""
    main_comment = thread['main']
    replies = thread['replies']
    
    # Send main comment
    main_comment_msg = await send_single_comment(
        msg, main_message_id, main_comment, conf_id, viewer_id, is_reply=False
    )
    
    if not main_comment_msg:
        return
    
    # Send replies indented under main comment
    for reply in replies[:3]:  # Limit to 3 replies per thread
        await send_single_comment(
            msg, main_comment_msg.message_id, reply, conf_id, viewer_id, is_reply=True
        )
    
    if len(replies) > 3:
        await msg.answer(
            f"*... and {len(replies) - 3} more replies*",
            reply_to_message_id=main_comment_msg.message_id,
            parse_mode="Markdown"
        )

async def send_single_comment(msg: types.Message, reply_to_id: int, comment: dict, conf_id: str, viewer_id: int, is_reply: bool = False):
    """Sends a single comment with proper formatting."""
    comment_author_id = comment.get('user_id')
    profile = get_user_profile(comment_author_id)
    karma_doc = karma_col.find_one({"_id": comment_author_id}) or {}
    aura_points = karma_doc.get('karma', 0)
    
    nickname = profile.get("nickname", DEFAULT_NICKNAME)
    emoji = profile.get("emoji", DEFAULT_EMOJI)
    
    c_likes = comment.get('likes', 0)
    c_dislikes = comment.get('dislikes', 0)
    
    # Format comment text - REMOVED like/dislike counts from message text
    indent = "  └─ " if is_reply else ""
    comment_text = (
        f"{indent}{emoji} **{nickname}** ✨({aura_points})\n"
        f"{indent}{comment.get('text', '')}"
    )
    
    # Create keyboard for the comment
    comment_kb = get_comment_keyboard(conf_id, comment, viewer_id, comment_author_id, c_likes, c_dislikes)
    
    try:
        sent_message = await msg.answer(
            comment_text,
            reply_to_message_id=reply_to_id,
            reply_markup=comment_kb,
            parse_mode="Markdown"
        )
        return sent_message
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
    
    # User profile button (only if not own comment)
    if comment_author_id != viewer_id:
        builder.button(text="👤 Profile", callback_data=f"view_profile:{comment_author_id}")
    
    builder.adjust(3, 2)
    return builder.as_markup()

async def show_confession_and_comments(msg: types.Message, conf_id: str):
    """
    Fetches, formats, and displays the confession post and its comments in a clean threaded format.
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
    tags_text = ' '.join([f'#{t.replace(" ", "_")}' for t in doc.get("tags", [])])
    
    main_confession_text = doc.get('text', '') 
    
    conf_text = (
        f"**📜 Confession #{doc.get('number')}**\n\n"
        f"{main_confession_text}\n\n"
        f"`{tags_text}`"
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

    # --- 4. Send Comments in Threaded Format ---
    if comments:
        # Organize comments into threads
        threads = organize_comments_into_threads(comments)
        
        # Send each thread
        for thread in threads[:5]:  # Limit to 5 threads for performance
            await send_comment_thread(msg, main_message_id, thread, conf_id, user_id)
        
        if len(threads) > 5:
            await msg.answer(
                f"*... and {len(threads) - 5} more comment threads not shown.*",
                reply_to_message_id=main_message_id,
                parse_mode="Markdown"
            )

# -------------------------
# User Profile Viewing System
# -------------------------

@dp.callback_query(F.data.startswith("view_profile:"))
async def cb_view_profile(callback: types.CallbackQuery):
    """Shows public profile of another user."""
    await callback.answer()
    
    try:
        target_user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Invalid user profile.")
        return
    
    viewer_id = callback.from_user.id
    
    # Get target user's profile
    profile = get_user_profile(target_user_id)
    karma_doc = karma_col.find_one({"_id": target_user_id}) or {}
    karma_score = karma_doc.get('karma', 0)
    
    # Format public profile
    profile_text = format_public_profile_message(profile, karma_score)
    kb = get_user_profile_keyboard(target_user_id, viewer_id)
    
    await callback.message.answer(profile_text, reply_markup=kb, parse_mode="Markdown")

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
    """Confirms and sends the report to admins."""
    await callback.answer()
    
    try:
        target_user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Invalid user.")
        return
    
    data = await state.get_data()
    reporter_id = callback.from_user.id
    
    # Send report to all admins
    report_text = (
        f"🚨 **USER REPORT**\n\n"
        f"👤 **Reported User ID:** `{target_user_id}`\n"
        f"👮 **Reporter ID:** `{reporter_id}`\n"
        f"⏰ **Time:** {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"**Reason:**\n{data.get('report_reason', 'No reason provided')}"
    )
    
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
        "gender_other": "Other",
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
# /start - Updated to handle deep links and FSM context
# -------------------------
@dp.message(Command("start"))
async def cmd_start(msg: types.Message, command: CommandObject, state: FSMContext): 
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
            InlineKeyboardButton(text=f"👍 {likes}", callback_data=f"vote:like:{conf_id}"),
            InlineKeyboardButton(text=f"👎 {dislikes}", callback_data=f"vote:dislike:{conf_id}")
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
            f"↩️ **Replying to {parent_anon_id}**\n"
            f"> {parent_text_preview}\n\n"
            "Please type your reply now. It will be posted anonymously."
        )


# -------------------------
# COMMENT/REPLY FLOW SUBMIT (Receiving the comment/reply text) (No change)
# -------------------------
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
    current_vote = voters.get(str_user_id), 0 # Use str(user_id) for consistent key in dict
    
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
            InlineKeyboardButton(text=f"👍 {new_likes}", callback_data=f"vote:like:{conf_id}"),
            InlineKeyboardButton(text=f"👎 {new_dislikes}", callback_data=f"vote:dislike:{conf_id}")
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
        f"Min {MIN_NICKNAME_LENGTH}, Max {MAX_NICKNAME_LENGTH} characters. Use letters and numbers only."
    )

@dp.message(ProfileStates.editing_nickname)
async def handle_new_nickname(msg: types.Message, state: FSMContext):
    new_nickname = msg.text.strip()
    user_id = msg.from_user.id
    
    if not new_nickname or len(new_nickname) < MIN_NICKNAME_LENGTH:
        await msg.answer(f"Your nickname is too short (min {MIN_NICKNAME_LENGTH} characters). Please try again.")
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

@dp.callback_query(lambda c: c.data and not c.data.startswith('vote:') and not c.data.startswith('comment_start:') and not c.data.startswith('cmt_vote:') and c.data != "profile_edit_bio" and not c.data.startswith("set_emoji:") and c.data not in ["profile_view", "profile_edit", "edit_nickname", "edit_bio", "change_emoji", "privacy_settings", "toggle_bio_privacy", "toggle_gender_privacy", "set_gender"] and not c.data.startswith("gender_") and not c.data.startswith("view_profile:") and not c.data.startswith("report_user:") and not c.data.startswith("request_chat:") and not c.data.startswith("confirm_report:") and not c.data.startswith("send_chat_request:") and not c.data.startswith("accept_chat_request:") and not c.data.startswith("decline_chat_request:"))
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
async def main():
    print(f"Bot starting... Auto-Approval is: {'ENABLED' if GLOBAL_AUTO_APPROVE else 'DISABLED'}")

    # Run both bot and web server together
    await asyncio.gather(
        dp.start_polling(bot, drop_pending_updates=True),
        start_web_server()
    )


if __name__ == "__main__":
    asyncio.run(main())

