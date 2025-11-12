import os
import asyncio
import random
import time
from datetime import datetime, UTC 
from dotenv import load_dotenv
import re
from typing import List, Dict, Any

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
ADMIN_IDS = [int(id) for id in os.getenv("ADMIN_IDS", "905781541").split(',') if id]
# Channel ID (Example: -1001234567890) - REPLACE WITH YOUR CHANNEL ID
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1001234567890"))
# Group ID (Example: -1009876543210) - REPLACE WITH YOUR GROUP CHAT ID
GROUP_ID = int(os.getenv("GROUP_ID", "-1009876543210"))
# Moderator/Approval Chat ID (Usually a private group for approvals)
APPROVAL_CHAT_ID = int(os.getenv("APPROVAL_CHAT_ID", "-1001122334455"))

# Auto-approval setting (0=off, 1=on)
AUTO_APPROVAL_ENABLED = os.getenv("AUTO_APPROVAL_ENABLED", "0") == "1"

# -------------------------
# DB Setup (Note: Removed 'Karma' collection)
# -------------------------
client = MongoClient(MONGO_URI)
db = client["confessionBot"]
conf_col = db["Confessions"]
settings_col = db["Settings"] 
users_col = db["Users"] # This will now store Aura Points

# -------------------------
# Bot and Dispatcher Initialization
# -------------------------
bot = Bot(token=BOT_TOKEN, parse_mode="MarkdownV2")
dp = Dispatcher()

# -------------------------
# FSM States
# -------------------------
class SubmissionStates(StatesGroup):
    waiting_for_confession = State()

class CommentStates(StatesGroup):
    waiting_for_submission = State()

# -------------------------
# Utility Functions
# -------------------------

def get_user_profile(user_id: int) -> Dict[str, Any]:
    """Retrieves or creates a user profile document, initializing aura_points."""
    # Try to find the user profile
    profile = users_col.find_one({"_id": user_id})
    
    if profile is None:
        # Create a new profile if it doesn't exist
        new_profile = {
            "_id": user_id,
            "joined_at": datetime.now(UTC),
            "aura_points": 0,  # Initialize Aura Points here
            "username_history": [],
            "confessions_submitted": 0,
            "comments_submitted": 0,
            "anon_nicknames": {}, # Store nicknames used per confession ID
            "profile_emoji": random.choice(["🌟", "✨", "💫", "⚡️", "🌙", "☀️", "🌈"]),
            "profile_name": f"Anonymous {random.randint(1000, 9999)}"
        }
        users_col.insert_one(new_profile)
        return new_profile
    return profile

def get_profile_anon_name(user_id: int, conf_id: str) -> str:
    """Gets the user's anonymous identity for a specific confession."""
    profile = get_user_profile(user_id)
    
    # Check if a unique anonymous name has already been assigned for this confession ID
    if conf_id in profile.get("anon_nicknames", {}):
        return profile["anon_nicknames"][conf_id]
        
    # Generate a new unique nickname for this confession
    anon_nicknames = profile.get("anon_nicknames", {})
    
    # Count how many confessions this user has interacted with
    nickname_index = len(anon_nicknames) + 1 
    
    # Use the base profile emoji and a simple numerical index
    base_emoji = profile.get("profile_emoji", "👤")
    
    new_anon_name = f"{base_emoji} Anonymous {nickname_index}"
    
    # Save the new unique nickname to the user's profile
    anon_nicknames[conf_id] = new_anon_name
    users_col.update_one(
        {"_id": user_id},
        {"$set": {"anon_nicknames": anon_nicknames}}
    )
    return new_anon_name

def format_profile_message(profile: Dict[str, Any], user_id: int, aura_score: int) -> str:
    """Formats the profile display message."""
    
    submission_count = profile.get("confessions_submitted", 0)
    comment_count = profile.get("comments_submitted", 0)
    joined_date = profile.get("joined_at", datetime.now(UTC)).strftime("%d %b %Y")

    # The anonymous name used in the profile view is consistent, based on ID
    # This is different from the names used in the comment threads.
    base_name = profile.get("profile_name", "Anonymous User")
    base_emoji = profile.get("profile_emoji", "👤")
    
    text = (
        f"**{base_emoji} {base_name}'s Confession Profile**\n"
        f"\\-\\- A fully anonymous profile linked to your Telegram ID\n\n"
        f"**🌟 Aura Points:** {aura_score}\n"
        f"**✍️ Confessions Submitted:** {submission_count}\n"
        f"**💬 Comments Submitted:** {comment_count}\n"
        f"**🗓️ Joined Since:** {joined_date}\n\n"
        f"`Your ID: {user_id}`"
    )
    return text

def get_confession_document(conf_id: str):
    """Retrieves a confession document by its string ID."""
    try:
        return conf_col.find_one({"_id": ObjectId(conf_id)})
    except Exception:
        return None

def create_reaction_keyboard(conf_id: str, likes: int, dislikes: int) -> InlineKeyboardMarkup:
    """Creates the inline keyboard for the channel post and profile view."""
    # Ensure the URL is correctly formatted for deep linking
    deep_link = f"https://t.me/{BOT_USERNAME}?start=comment_{conf_id}"
    
    builder = InlineKeyboardBuilder()
    
    # Row 1: Like/Dislike with current counts
    builder.row(
        InlineKeyboardButton(text=f"👍 {likes}", callback_data=f"vote:like:{conf_id}"),
        InlineKeyboardButton(text=f"👎 {dislikes}", callback_data=f"vote:dislike:{conf_id}"),
        width=2
    )
    
    # Row 2: View/Add Comment link (uses deep link to open private chat)
    builder.row(
        InlineKeyboardButton(text="💬 View / Add Comment", url=deep_link)
    )
    
    return builder.as_markup()

def create_comment_keyboard(conf_id: str, comment_index: int, likes: int, dislikes: int) -> InlineKeyboardMarkup:
    """Creates the inline keyboard for a specific comment message in the private chat."""
    builder = InlineKeyboardBuilder()
    
    # Row 1: Like/Dislike with current counts
    builder.row(
        InlineKeyboardButton(text=f"👍 {likes}", callback_data=f"cmt_vote:like:{conf_id}:{comment_index}"),
        InlineKeyboardButton(text=f"👎 {dislikes}", callback_data=f"cmt_vote:dislike:{conf_id}:{comment_index}"),
        width=2
    )

    # Row 2: Reply Button
    builder.row(
        InlineKeyboardButton(text="↩️ Reply", callback_data=f"comment_start:{conf_id}:{comment_index}")
    )
    
    return builder.as_markup()

def create_add_comment_keyboard(conf_id: str) -> InlineKeyboardMarkup:
    """Creates the keyboard for the main confession message in the private chat."""
    # The parent index is -1 for a new top-level comment
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✍️ + Add Comment", callback_data=f"comment_start:{conf_id}:-1")
    )
    return builder.as_markup()

async def delete_old_thread(chat_id: int, conf_id: str):
    """Deletes all messages related to a previous thread view for a confession ID."""
    try:
        settings = settings_col.find_one({"_id": "thread_messages"}) or {}
        thread_messages = settings.get("messages", {}).get(str(chat_id), {})
        
        if conf_id in thread_messages:
            for message_id in thread_messages[conf_id]:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=message_id)
                except TelegramAPIError:
                    # Ignore messages that are already deleted
                    pass
            # Clear the list after deletion
            thread_messages[conf_id] = []
            settings_col.update_one(
                {"_id": "thread_messages"},
                {"$set": {f"messages.{chat_id}": thread_messages}},
                upsert=True
            )
    except Exception as e:
        print(f"Error during thread deletion: {e}")

async def save_thread_message_id(chat_id: int, conf_id: str, message_id: int):
    """Saves a message ID to the database for later thread cleanup."""
    settings_col.update_one(
        {"_id": "thread_messages"},
        {"$push": {f"messages.{chat_id}.{conf_id}": message_id}},
        upsert=True
    )

def generate_anon_id_map(comments: List[Dict[str, Any]]) -> Dict[int, str]:
    """Generates consistent anonymous IDs for all unique comment authors."""
    anon_map = {}
    unique_user_ids = []
    
    # 1. Collect all unique user IDs
    for comment in comments:
        user_id = comment["user_id"]
        if user_id not in unique_user_ids:
            unique_user_ids.append(user_id)
            
    # 2. Assign a consistent anon ID for this thread
    for i, user_id in enumerate(unique_user_ids):
        # We start counting from 1 for display purposes
        anon_map[user_id] = f"👤 Commenter {i+1}" 
        
    return anon_map

async def show_confession_and_comments(request_obj: types.Message | types.CallbackQuery, conf_id: str):
    """
    Renders the confession and its comment thread in the user's private chat.
    This function deletes the old thread and sends a new one with updated information.
    """
    if isinstance(request_obj, types.CallbackQuery):
        msg = request_obj.message
        await request_obj.answer()
    else:
        msg = request_obj
        
    chat_id = msg.chat.id
    
    doc = get_confession_document(conf_id)
    if not doc or not doc.get("approved"):
        await msg.answer("Confession not found or not yet approved.")
        return

    # 1. Clean up old thread messages
    await delete_old_thread(chat_id, conf_id)

    # 2. Extract Data
    confession_text = doc["text"]
    comments = doc.get("comments", [])
    
    # 3. Generate consistent anonymous map for the thread
    # Include the main author's ID in the map for consistency
    all_user_ids = [doc["user_id"]] + [c["user_id"] for c in comments]
    anon_map = {}
    unique_user_ids = []
    for user_id in all_user_ids:
        if user_id not in unique_user_ids:
            unique_user_ids.append(user_id)
    
    for i, user_id in enumerate(unique_user_ids):
        # Assign 'Author' to the confession user, otherwise 'Commenter X'
        if user_id == doc["user_id"]:
            anon_map[user_id] = "🖋️ Author"
        else:
            anon_map[user_id] = f"👤 Commenter {i}" # Start other commenters from 1 for simplicity

    # 4. Format Main Confession Message
    post_author_id = doc["user_id"]
    formatted_confession_text = (
        f"**Confession \\#{doc.get('id_num', 'N/A')}**\n"
        f"**{anon_map.get(post_author_id, 'Author')}**\n"
        f"\\-\\- [Posted {doc['approved_at'].strftime('%d %b %Y')}]\n\n"
        f"{confession_text}\n\n"
        f"_\\-\\_\\-_\\-_\\-_\\-_\\-_\\-_\\-_\\-_\\-\\_\\-_\\-_\\-\\_\\-_\\-_\\-\\_\\-_"
    )
    
    # 5. Send Main Confession Message
    main_msg = await bot.send_message(
        chat_id=chat_id,
        text=formatted_confession_text,
        reply_markup=create_add_comment_keyboard(conf_id)
    )
    await save_thread_message_id(chat_id, conf_id, main_msg.message_id)
    
    message_map = {}
    message_map[-1] = main_msg.message_id # -1 maps to the main confession for replies

    # 6. Send Comments
    for i, comment in enumerate(comments):
        # Determine the parent message to reply to
        parent_index = comment.get("parent_index", -1)
        reply_to_id = message_map.get(parent_index, main_msg.message_id)
        
        # Get the commenter's consistent anonymous ID
        commenter_anon_name = anon_map.get(comment["user_id"], f"👤 Commenter {i+1}")
        
        # Check if this comment is a reply to another comment
        replying_to_text = ""
        if parent_index != -1:
            # We need the anon name of the comment being replied to
            parent_comment = comments[parent_index]
            parent_anon_name = anon_map.get(parent_comment["user_id"], "Commenter")
            replying_to_text = f"↩️ Replying to **{parent_anon_name}**\n"

        comment_text = (
            f"**{commenter_anon_name}**\n"
            f"{replying_to_text}"
            f"{comment['text']}"
        )

        likes = comment.get("likes", 0)
        dislikes = comment.get("dislikes", 0)

        comment_msg = await bot.send_message(
            chat_id=chat_id,
            text=comment_text,
            reply_to_message_id=reply_to_id,
            reply_markup=create_comment_keyboard(conf_id, i, likes, dislikes)
        )
        await save_thread_message_id(chat_id, conf_id, comment_msg.message_id)
        
        # Store the current message ID for future replies
        message_map[i] = comment_msg.message_id

# -------------------------
# Handlers (Commands and FSM)
# -------------------------

# --- Start / Help ---
@dp.message(Command("start"))
async def cmd_start(msg: types.Message, state: FSMContext, command: CommandObject):
    # Check for deep link payload (e.g., /start comment_65278c544e45d15c7e3f898c)
    if command.args and command.args.startswith("comment_"):
        conf_id = command.args.split("_", 1)[1]
        await state.clear()
        
        # Directly call the thread viewer
        await show_confession_and_comments(msg, conf_id)
        return

    # Regular /start and /help command logic
    await state.clear()
    
    is_admin = msg.from_user.id in ADMIN_IDS
    admin_info = "You are an **Admin**\\." if is_admin else ""
    
    help_text = (
        f"👋 Welcome to the **{BOT_USERNAME}** Anonymous Confession Bot\\!\n\n"
        f"**Commands:**\n"
        f"\\- /confess \\- Start submitting a new anonymous confession\\.\n"
        f"\\- /profile \\- View your anonymous profile and Aura Points\\.\n"
        f"\\- /my\\_karma \\- View your current Aura Points score\\.\n"
        f"\\- /latest \\- View the 5 most recent approved confessions in this chat\\.\n"
        f"\\- /random \\- View a random approved confession in this chat\\.\n"
        f"\\- /help \\- Show this message\\.\n\n"
        f"**How to Comment & Vote:**\n"
        f"1\\. Go to the main confession channel\\.\n"
        f"2\\. Click the **'💬 View / Add Comment'** button below any post\\.\n"
        f"3\\. This chat will display the full thread where you can vote on comments or reply anonymously\\.\n\n"
        f"{admin_info}"
    )
    
    await msg.answer(help_text, disable_web_page_preview=True)


# --- Confess FSM ---
@dp.message(Command("confess"))
async def cmd_confess(msg: types.Message, state: FSMContext):
    if msg.chat.type != "private":
        await msg.reply("Please start your submission in a private chat with the bot\\.")
        return
        
    await state.set_state(SubmissionStates.waiting_for_confession)
    await msg.answer(
        "📝 **Start Your Anonymous Confession**\n"
        "Please type and send your confession now\\. Remember, it will be posted fully anonymously after review\\."
    )

@dp.message(SubmissionStates.waiting_for_confession)
async def process_confession(msg: types.Message, state: FSMContext):
    await state.clear()
    
    text = msg.text
    user_id = msg.from_user.id
    
    if not text or len(text) < 10:
        await msg.answer("Your confession is too short\\. Please write at least 10 characters\\.")
        return
    
    # Get next sequential ID number
    id_doc = settings_col.find_one_and_update(
        {"_id": "confession_id_counter"},
        {"$inc": {"next_id": 1}},
        upsert=True,
        return_document=True
    )
    conf_id_num = id_doc["next_id"] - 1 # ID is 1-based, start at 1

    # Create document
    doc = {
        "id_num": conf_id_num,
        "text": text,
        "user_id": user_id,
        "submitted_at": datetime.now(UTC),
        "approved": False,
        "likes": 0,
        "dislikes": 0,
        "comments": [], # Array to store comment objects
        "voters": {} # Tracks user_id -> vote_type (like/dislike) for post
    }
    
    result = conf_col.insert_one(doc)
    conf_id = str(result.inserted_id)
    
    # Update user submission count
    users_col.update_one({"_id": user_id}, {"$inc": {"confessions_submitted": 1}}, upsert=True)
    
    # ------------------
    # Approval Logic
    # ------------------
    if AUTO_APPROVAL_ENABLED:
        # Auto-Approve logic
        doc["approved"] = True
        doc["approved_at"] = datetime.now(UTC)
        conf_col.update_one({"_id": result.inserted_id}, {"$set": {"approved": True, "approved_at": doc["approved_at"]}})
        
        await msg.answer(
            f"✅ **Confession #{conf_id_num} Auto-Approved!**\n"
            f"Your confession has been posted to the channel\\."
        )
        
        # Publish the confession to the channel and group
        await publish_confession(doc)
        
    else:
        # Manual Approval logic
        await msg.answer(
            f"⏳ **Confession #{conf_id_num} Submitted for Review**\n"
            "Your submission has been received and will be reviewed by an admin shortly\\."
        )

        # Build the admin approval message and keyboard
        admin_text = (
            f"**NEW CONFESSION SUBMISSION \\(ID: {conf_id}\\)**\n"
            f"**From User ID:** `{user_id}`\n"
            f"**Confession \\#{conf_id_num}**\n\n"
            f"{text}"
        )
        
        admin_kb = InlineKeyboardBuilder()
        admin_kb.row(
            InlineKeyboardButton(text="✅ Approve", callback_data=f"approve:{conf_id}"),
            InlineKeyboardButton(text="❌ Reject", callback_data=f"reject:{conf_id}")
        )

        try:
            await bot.send_message(
                chat_id=APPROVAL_CHAT_ID,
                text=admin_text,
                reply_markup=admin_kb.as_markup()
            )
        except Exception as e:
            print(f"Failed to send approval message to chat {APPROVAL_CHAT_ID}: {e}")
            await msg.answer("❌ Admin alert failed\\. Please contact an admin manually\\.")

# --- Admin Callback Handlers for Approval ---
@dp.callback_query(lambda c: c.data and (c.data.startswith('approve:') or c.data.startswith('reject:')))
async def cb_handle_approval(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Only admins can manage submissions.", show_alert=True)
        return

    action, conf_id = callback.data.split(":")
    doc = conf_col.find_one({"_id": ObjectId(conf_id)})

    if not doc:
        await callback.message.edit_text(f"Error: Confession ID {conf_id} not found.")
        return

    user_id = doc["user_id"]
    
    if action == "approve" and not doc["approved"]:
        # Update document state
        conf_col.update_one(
            {"_id": doc["_id"]},
            {"$set": {"approved": True, "approved_at": datetime.now(UTC)}}
        )
        doc["approved"] = True
        doc["approved_at"] = datetime.now(UTC)
        
        # Notify user
        try:
            await bot.send_message(
                chat_id=user_id, 
                text=f"✅ **Confession \\#{doc['id_num']} Approved!**\nIt has been posted to the channel\\."
            )
        except TelegramForbiddenError:
            # Bot was blocked by the user
            pass
        except Exception as e:
            print(f"Error sending approval notification to user {user_id}: {e}")

        # Publish the confession
        await publish_confession(doc)

        # Edit admin message
        await callback.message.edit_text(
            f"✅ **APPROVED by {callback.from_user.username}**:\n\n{callback.message.text}",
            reply_markup=None
        )

    elif action == "reject" and not doc["approved"]:
        # Update document state (optional, just to record)
        conf_col.update_one(
            {"_id": doc["_id"]},
            {"$set": {"rejected_at": datetime.now(UTC)}}
        )
        
        # Notify user
        try:
            await bot.send_message(
                chat_id=user_id, 
                text=f"❌ **Confession \\#{doc['id_num']} Rejected**\\.\nIt did not meet the guidelines\\. Please re\\-read /help\\."
            )
        except TelegramForbiddenError:
            # Bot was blocked by the user
            pass
        except Exception as e:
            print(f"Error sending rejection notification to user {user_id}: {e}")

        # Edit admin message
        await callback.message.edit_text(
            f"❌ **REJECTED by {callback.from_user.username}**:\n\n{callback.message.text}",
            reply_markup=None
        )

    else:
        await callback.answer("Action already taken or invalid state.", show_alert=True)
        
# --- Confession Publishing Logic ---
async def publish_confession(doc: Dict[str, Any]):
    """Publishes the final approved confession to the channel and group."""
    conf_id = str(doc["_id"])
    conf_id_num = doc["id_num"]
    
    # Final format for channel post
    post_text = (
        f"**Confession \\#{conf_id_num}**\n\n"
        f"{doc['text']}\n\n"
        f"\\-\\- [Posted {doc['approved_at'].strftime('%d %b %Y')}]"
    )

    # Build the keyboard for voting and commenting (Deep Link)
    likes = doc.get("likes", 0)
    dislikes = doc.get("dislikes", 0)
    reaction_kb = create_reaction_keyboard(conf_id, likes, dislikes)
    
    # 1. Post to the main channel
    try:
        channel_msg = await bot.send_message(
            chat_id=CHANNEL_ID,
            text=post_text,
            reply_markup=reaction_kb
        )
        # Save the channel message ID to the confession document
        conf_col.update_one(
            {"_id": doc["_id"]}, 
            {"$set": {"channel_message_id": channel_msg.message_id}}
        )
        
    except Exception as e:
        print(f"Failed to post to channel {CHANNEL_ID}: {e}")
        return

    # 2. Post to the discussion group (optional, requires linking)
    try:
        # Note: If the group is linked to the channel, this step may be redundant 
        # as the channel post automatically forwards. You can uncomment this 
        # if you want a separate message in the discussion group.
        # await bot.send_message(
        #     chat_id=GROUP_ID,
        #     text=post_text
        # )
        pass
    except Exception as e:
        print(f"Failed to post to group {GROUP_ID}: {e}")

# --- Post Voting Handler (Updates Likes/Dislikes) ---
@dp.callback_query(lambda c: c.data and c.data.startswith('vote:'))
async def cb_handle_vote(callback: types.CallbackQuery):
    try:
        action, vote_type, conf_id = callback.data.split(":")
        user_id = callback.from_user.id
        
        doc = get_confession_document(conf_id)
        if not doc:
            await callback.answer("Confession not found.")
            return

        current_voters = doc.get("voters", {})
        
        old_vote = current_voters.get(str(user_id))
        
        # Initialize changes
        karma_change = 0
        update_inc = {}
        
        if old_vote == vote_type:
            # User is revoking their vote
            current_voters.pop(str(user_id))
            if vote_type == "like":
                update_inc["likes"] = -1
                karma_change = -1
            else:
                update_inc["dislikes"] = -1
                karma_change = +1 # Dislike removal increases karma
            msg = "Vote revoked."
            
        elif old_vote:
            # User is switching their vote (e.g., from like to dislike)
            current_voters[str(user_id)] = vote_type
            if vote_type == "like":
                update_inc["likes"] = 1
                update_inc["dislikes"] = -1
                karma_change = 2 # Dislike removed (+1), Like added (+1)
            else: # new vote is dislike
                update_inc["dislikes"] = 1
                update_inc["likes"] = -1
                karma_change = -2 # Like removed (-1), Dislike added (-1)
            msg = "Vote changed."
            
        else:
            # User is making a new vote
            current_voters[str(user_id)] = vote_type
            if vote_type == "like":
                update_inc["likes"] = 1
                karma_change = 1
            else:
                update_inc["dislikes"] = 1
                karma_change = -1
            msg = "Vote cast."

        # 1. Update the Confession document with new votes and voter list
        update_set = {"voters": current_voters}
        update_operation = {"$inc": update_inc, "$set": update_set}
        conf_col.update_one({"_id": doc["_id"]}, update_operation)
        
        # Recalculate current counts for the keyboard update
        new_likes = doc.get("likes", 0) + update_inc.get("likes", 0)
        new_dislikes = doc.get("dislikes", 0) + update_inc.get("dislikes", 0)
        
        # 2. Update Karma (Aura Points) for Confessor 
        confessor_id = doc["user_id"]
        if karma_change != 0:
            # 🟢 FIX: Update the dedicated 'aura_points' field in the 'users_col'
            users_col.update_one( 
                {"_id": confessor_id},
                {"$inc": {"aura_points": karma_change}},
                upsert=True
            )

        # 3. Update the Reaction Keyboard on the Channel Message
        new_reaction_kb = create_reaction_keyboard(conf_id, new_likes, new_dislikes)
        channel_message_id = doc.get("channel_message_id")
        
        if channel_message_id:
            try:
                await bot.edit_message_reply_markup(
                    chat_id=CHANNEL_ID,
                    message_id=channel_message_id,
                    reply_markup=new_reaction_kb
                )
            except TelegramBadRequest as e:
                # Ignore "message is not modified" error
                if "message is not modified" not in str(e):
                    print(f"Error editing message {channel_message_id}: {e}")

        await callback.answer(msg)

    except Exception as e:
        print(f"Error in cb_handle_vote: {e}")
        await callback.answer("An error occurred during voting.")

# --- Comment Submission FSM Handlers ---

@dp.callback_query(lambda c: c.data and c.data.startswith('comment_start:'))
async def cb_comment_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    
    # comment_start:{conf_id}:{parent_index}
    action, conf_id, parent_index_str = callback.data.split(":")
    parent_index = int(parent_index_str)
    
    doc = get_confession_document(conf_id)
    if not doc:
        await callback.message.answer("Confession not found or closed for comments.")
        return
        
    # Store context in FSM
    await state.update_data(conf_id=conf_id, parent_index=parent_index)
    await state.set_state(CommentStates.waiting_for_submission)
    
    if parent_index == -1:
        prompt = "📝 **New Top-Level Comment**\nType and send your comment now\\. It will be posted anonymously\\."
    else:
        # Get the commenter they are replying to
        comments = doc.get("comments", [])
        if parent_index < 0 or parent_index >= len(comments):
            prompt = "📝 **New Top-Level Comment**\nType and send your comment now\\. It will be posted anonymously\\."
        else:
            # Generating consistent anon IDs is complex here, simpler to just say "Commenter"
            anon_id_map = generate_anon_id_map(comments)
            parent_comment_user_id = comments[parent_index]["user_id"]
            parent_anon_name = anon_id_map.get(parent_comment_user_id, "Commenter")
            
            prompt = (
                f"↩️ **Replying to {parent_anon_name}**\n"
                f"Type and send your reply now\\. It will be posted anonymously as part of the thread\\."
            )
            
    await callback.message.answer(prompt)

@dp.message(CommentStates.waiting_for_submission)
async def handle_comment_submission(msg: types.Message, state: FSMContext):
    
    if not msg.text:
        await msg.answer("Please send your comment as text only\\.")
        return

    data = await state.get_data()
    conf_id = data.get("conf_id")
    parent_index = data.get("parent_index", -1)
    
    await state.clear()
    
    doc = get_confession_document(conf_id)
    if not doc:
        await msg.answer("The confession could not be found or the comment period has ended\\.")
        return

    user_id = msg.from_user.id
    
    # 1. Get the anonymous name for this user on this confession thread
    anon_name = get_profile_anon_name(user_id, conf_id)
    
    # 2. Prepare the new comment object
    new_comment = {
        "user_id": user_id,
        "text": msg.text,
        "submitted_at": datetime.now(UTC),
        "likes": 0,
        "dislikes": 0,
        "parent_index": parent_index, # Index of the comment being replied to, or -1
        "voters": {} # Tracks user_id -> vote_type (like/dislike) for this comment
    }
    
    # 3. Add the comment to the database
    conf_col.update_one(
        {"_id": doc["_id"]},
        {"$push": {"comments": new_comment}}
    )
    
    # 4. Update user comment count
    users_col.update_one({"_id": user_id}, {"$inc": {"comments_submitted": 1}}, upsert=True)
    
    # 5. Re-render the thread to show the new comment
    await msg.answer("✅ **Comment Posted!**\nUpdating thread now\\.")
    await show_confession_and_comments(msg, conf_id)


# --- Comment Voting Handler (Updates Comment Likes/Dislikes) ---
@dp.callback_query(lambda c: c.data and c.data.startswith('cmt_vote:'))
async def cb_handle_comment_vote(callback: types.CallbackQuery):
    try:
        # cmt_vote:like:{conf_id}:{comment_index}
        action, vote_type, conf_id, comment_index_str = callback.data.split(":")
        comment_index = int(comment_index_str)
        user_id = callback.from_user.id
        
        doc = get_confession_document(conf_id)
        if not doc:
            await callback.answer("Confession not found.")
            return

        comments = doc.get("comments", [])
        if comment_index < 0 or comment_index >= len(comments):
            await callback.answer("Comment not found.")
            return
            
        comment = comments[comment_index]
        current_voters = comment.get("voters", {})
        
        old_vote = current_voters.get(str(user_id))
        
        # Initialize changes
        karma_change = 0
        update_inc = {}
        msg = "Vote updated."
        
        if old_vote == vote_type:
            # User is revoking their vote
            current_voters.pop(str(user_id))
            if vote_type == "like":
                update_inc[f"comments.{comment_index}.likes"] = -1
                karma_change = -1
            else:
                update_inc[f"comments.{comment_index}.dislikes"] = -1
                karma_change = +1 # Dislike removal increases karma
            msg = "Vote revoked."
            
        elif old_vote:
            # User is switching their vote (e.g., from like to dislike)
            current_voters[str(user_id)] = vote_type
            if vote_type == "like":
                update_inc[f"comments.{comment_index}.likes"] = 1
                update_inc[f"comments.{comment_index}.dislikes"] = -1
                karma_change = 2 # Dislike removed (+1), Like added (+1)
            else: # new vote is dislike
                update_inc[f"comments.{comment_index}.dislikes"] = 1
                update_inc[f"comments.{comment_index}.likes"] = -1
                karma_change = -2 # Like removed (-1), Dislike added (-1)
            msg = "Vote changed."
            
        else:
            # User is making a new vote
            current_voters[str(user_id)] = vote_type
            if vote_type == "like":
                update_inc[f"comments.{comment_index}.likes"] = 1
                karma_change = 1
            else:
                update_inc[f"comments.{comment_index}.dislikes"] = 1
                karma_change = -1
            msg = "Vote cast."

        # 1. Update the Comment voters map and vote counts using dot notation
        update_set = {f"comments.{comment_index}.voters": current_voters}
        update_operation = {"$inc": update_inc, "$set": update_set}
        conf_col.update_one({"_id": doc["_id"]}, update_operation)
        
        # 2. Update Karma (Aura Points) for Comment Author 
        comment_author_id = comment["user_id"]
        if karma_change != 0:
            # 🟢 FIX: Update the dedicated 'aura_points' field in the 'users_col'
            users_col.update_one( 
                {"_id": comment_author_id},
                {"$inc": {"aura_points": karma_change}},
                upsert=True
            )

        await callback.answer(msg)
        
        # 3. Send a new chain of messages to reflect updated vote counts
        await show_confession_and_comments(callback, conf_id)

    except Exception as e:
        print(f"Error in cb_handle_comment_vote: {e}")
        await callback.answer("An error occurred during comment voting.")

# --- View Profile ---
@dp.message(Command("profile"))
@dp.callback_query(F.data == "profile_view")
async def cmd_profile_view(request: types.Message | types.CallbackQuery, state: FSMContext):
    
    if isinstance(request, types.CallbackQuery):
        msg = request.message
        user_id = request.from_user.id
        await request.answer()
    else:
        msg = request
        user_id = msg.from_user.id
        
    if msg.chat.type != "private":
        await msg.reply("Please use this command in a private chat with the bot\\.")
        return
        
    await state.clear()
    
    # Get profile data (creates default if non-existent)
    profile = get_user_profile(user_id)
    
    # 🟢 FIX: Get Karma (Aura) score directly from the profile document
    karma_score = profile.get("aura_points", 0)
    
    # Format the message using the new function
    profile_text = format_profile_message(profile, user_id, karma_score)
    
    await msg.answer(profile_text)

# --- View My Karma (Aura) ---
@dp.message(Command("my_karma"))
async def cmd_my_karma(msg: types.Message):
    if msg.chat.type != "private":
        await msg.reply("Please use this command in a private chat with the bot.")
        return
    
    user_id = msg.from_user.id
    
    # 🟢 FIX: Get the score from the user profile document (users_col)
    profile_doc = users_col.find_one({"_id": user_id}) 
    karma_score = profile_doc.get("aura_points", 0) if profile_doc else 0
    
    await msg.answer(
        f"🌟 **Your Confession Aura Points**\n"
        f"You have accumulated **{karma_score} points** from your approved confessions and comments\\.\n"
        "Points are earned when your posts or comments are liked \\(+1\\) or disliked \\(\\-1\\)\\."
    )

# --- View Latest Confessions ---
@dp.message(Command("latest"))
async def cmd_latest(msg: types.Message):
    if msg.chat.type != "private":
        await msg.reply("Please use this command in a private chat with the bot\\.")
        return
    
    # Find the 5 most recently approved confessions
    docs = list(conf_col.find({"approved": True}).sort("approved_at", -1).limit(5))
    if not docs:
        await msg.answer("No approved confessions yet\\.")
        return
        
    await msg.answer(f"🔎 Showing latest {len(docs)} confessions\\. Use the /start comment_<id> link on any post in the channel to see its full view\\.")
    
    # Send each one separately using the thread viewer
    for doc in docs:
        await show_confession_and_comments(msg, str(doc["_id"]))


# --- View Random Confession ---
@dp.message(Command("random"))
async def cmd_random(msg: types.Message):
    if msg.chat.type != "private":
        await msg.reply("Please use this command in a private chat with the bot\\.")
        return

    # Get total count of approved documents
    count = conf_col.count_documents({"approved": True})
    if count == 0:
        await msg.answer("No approved confessions yet\\.")
        return
    
    # Select a random skip index
    skip = random.randint(0, max(0, count-1))
    
    # Find one document at the random skip index
    docs = list(conf_col.find({"approved": True}).skip(skip).limit(1))
    if not docs:
        await msg.answer("No results\\.")
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
    # Use port 10000, common for deployment environments like Render
    site = web.TCPSite(runner, "0.0.0.0", 10000)
    await site.start()


# --- Main entry point ---
async def main():
    # Attempt to start the web server for keep-alive purposes
    try:
        await start_web_server()
        print("Web server started on 0.0.0.0:10000")
    except Exception as e:
        print(f"Warning: Could not start web server (likely due to environment constraints): {e}")
        
    print(f"Bot starting... Auto-Approval is: {'ON' if AUTO_APPROVAL_ENABLED else 'OFF'}")
    
    # Run the bot and skip outdated updates
    await dp.start_polling(bot, skip_update=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped by user.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
