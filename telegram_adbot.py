import os
import logging
import asyncio
import json
import time
import random
from typing import Dict, List, Optional, Set
from datetime import datetime, timedelta
from telethon import TelegramClient, errors, events
from telethon.tl.types import Channel, Chat, User
from telethon.tl.custom import Button
from dotenv import load_dotenv
import sqlite3
from dataclasses import dataclass, asdict
import traceback

# Load environment variables
load_dotenv()

DEFAULT_SEND_INTERVAL = 5
MAX_ACCOUNTS = 10
FLOOD_WAIT_TOLERANCE = 300  # 5 minutes max flood wait

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S"
)
logging.getLogger("telethon").setLevel(logging.CRITICAL)

@dataclass
class Account:
    name: str
    session_file: str
    status: str = "active"  # active, flood_wait, banned, error
    last_used: Optional[datetime] = None
    flood_wait_until: Optional[datetime] = None
    messages_sent: int = 0
    errors_count: int = 0
    phone_number: Optional[str] = None
    user_id: Optional[int] = None  # User who owns this account

@dataclass
class Campaign:
    id: str
    name: str
    messages: List[str]
    targets: List[Dict]
    mode: str  # groups, dms, both
    interval: int
    active: bool = False
    accounts: Optional[List[str]] = None  # Account names to use
    schedule: Optional[Dict] = None  # Start/end times, days
    filters: Optional[Dict] = None  # Target filters
    user_id: Optional[int] = None  # User who owns this campaign
    
class DatabaseManager:
    def __init__(self, db_path="adbot.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Accounts table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS accounts (
                name TEXT PRIMARY KEY,
                session_file TEXT,
                status TEXT DEFAULT 'active',
                last_used TEXT,
                flood_wait_until TEXT,
                messages_sent INTEGER DEFAULT 0,
                errors_count INTEGER DEFAULT 0,
                phone_number TEXT,
                user_id INTEGER
            )
        ''')
        
        # Check if user_id column exists in accounts table, add if not
        cursor.execute("PRAGMA table_info(accounts)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'user_id' not in columns:
            cursor.execute('ALTER TABLE accounts ADD COLUMN user_id INTEGER')
            logging.info("Added user_id column to accounts table")
        
        # Campaigns table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS campaigns (
                id TEXT PRIMARY KEY,
                name TEXT,
                data TEXT,
                created_at TEXT,
                last_run TEXT,
                user_id INTEGER
            )
        ''')
        
        # Check if user_id column exists in campaigns table, add if not
        cursor.execute("PRAGMA table_info(campaigns)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'user_id' not in columns:
            cursor.execute('ALTER TABLE campaigns ADD COLUMN user_id INTEGER')
            logging.info("Added user_id column to campaigns table")
        
        # Statistics table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS statistics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_name TEXT,
                campaign_id TEXT,
                target_id TEXT,
                target_type TEXT,
                message_sent BOOLEAN,
                timestamp TEXT,
                error TEXT
            )
        ''')
        
        # Blacklist table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS blacklist (
                target_id TEXT PRIMARY KEY,
                reason TEXT,
                added_at TEXT
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def save_account(self, account: Account):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO accounts 
            (name, session_file, status, last_used, flood_wait_until, messages_sent, errors_count, phone_number, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            account.name, account.session_file, account.status,
            account.last_used.isoformat() if account.last_used else None,
            account.flood_wait_until.isoformat() if account.flood_wait_until else None,
            account.messages_sent, account.errors_count, account.phone_number, account.user_id
        ))
        conn.commit()
        conn.close()
    
    def get_accounts(self) -> List[Account]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM accounts')
        rows = cursor.fetchall()
        conn.close()
        
        accounts = []
        for row in rows:
            account = Account(
                name=row[0], session_file=row[1], status=row[2],
                last_used=datetime.fromisoformat(row[3]) if row[3] else None,
                flood_wait_until=datetime.fromisoformat(row[4]) if row[4] else None,
                messages_sent=row[5], errors_count=row[6], phone_number=row[7],
                user_id=row[8] if len(row) > 8 else None
            )
            accounts.append(account)
        return accounts
    
    def save_campaign(self, campaign: Campaign):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO campaigns (id, name, data, created_at, user_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (campaign.id, campaign.name, json.dumps(asdict(campaign)), datetime.now().isoformat(), campaign.user_id))
        conn.commit()
        conn.close()
    
    def get_campaigns(self) -> List[Campaign]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT data FROM campaigns')
        rows = cursor.fetchall()
        conn.close()
        
        campaigns = []
        for row in rows:
            data = json.loads(row[0])
            campaign = Campaign(**data)
            campaigns.append(campaign)
        return campaigns
    
    def log_activity(self, account_name: str, campaign_id: str, target_id: str, target_type: str, success: bool, error: Optional[str] = None):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO statistics (account_name, campaign_id, target_id, target_type, message_sent, timestamp, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (account_name, campaign_id, str(target_id), target_type, success, datetime.now().isoformat(), error))
        conn.commit()
        conn.close()

class TelegramAdBot:
    def __init__(self):
        self.bot_token = os.getenv('BOT_TOKEN')
        # Remove admin restrictions - this is now a public bot
        self.authorized_users: Set[int] = set()  # Will track all users
        
        self.db = DatabaseManager()
        self.accounts: Dict[str, Account] = {}
        self.clients: Dict[str, TelegramClient] = {}
        self.campaigns: Dict[str, Campaign] = {}
        self.running_campaigns: Set[str] = set()
        
        self.bot: Optional[TelegramClient] = None
        self.user_state: Dict[int, Dict] = {}
        self.stats = {
            'total_sent': 0,
            'total_failed': 0,
            'active_campaigns': 0,
            'active_accounts': 0,
            'uptime_start': datetime.now()
        }
        
        # Load existing data
        self.load_accounts()
        self.load_campaigns()
    
    def get_user_accounts(self, user_id: int) -> List[Account]:
        """Get accounts owned by a specific user"""
        return [acc for acc in self.accounts.values() if acc.user_id == user_id]
    
    def get_user_campaigns(self, user_id: int) -> List[Campaign]:
        """Get campaigns owned by a specific user"""
        return [camp for camp in self.campaigns.values() if camp.user_id == user_id]
    
    def can_add_account(self, user_id: int) -> bool:
        """Check if user can add more accounts (unlimited for all users)"""
        return True  # Unlimited accounts for all users
    
    def add_user_to_authorized(self, user_id: int):
        """Add user to authorized users (public bot - all users are authorized)"""
        self.authorized_users.add(user_id)
    
    async def add_account_with_validation(self, user_id: int, account_name: str, session_file: str) -> bool:
        """Add account with user validation and 2-account limit"""
        # Check if user can add more accounts
        if not self.can_add_account(user_id):
            return False
        
        # Create account with user_id
        account = Account(
            name=account_name,
            session_file=session_file,
            user_id=user_id
        )
        
        # Initialize and validate the account
        if await self.init_account_client(account):
            self.accounts[account_name] = account
            self.db.save_account(account)
            return True
        
        return False
    
    def load_accounts(self):
        accounts = self.db.get_accounts()
        for account in accounts:
            self.accounts[account.name] = account
            # Clean up flood waits that have expired
            if account.flood_wait_until and datetime.now() > account.flood_wait_until:
                account.status = "active"
                account.flood_wait_until = None
                self.db.save_account(account)
    
    def load_campaigns(self):
        campaigns = self.db.get_campaigns()
        for campaign in campaigns:
            self.campaigns[campaign.id] = campaign
    
    async def init_bot(self):
        """Initialize bot client"""
        if not self.bot_token:
            logging.error("Bot token not provided")
            return False
        
        try:
            self.bot = TelegramClient(
                session="bot_session",
                api_id=23633832,
                api_hash="c045f1239bbf24f22f9e21e38a0c307c"
            )
            await self.bot.start(bot_token=self.bot_token)
            
            # Register all event handlers
            self.register_handlers()
            logging.info("Bot initialized successfully")
            return True
        except Exception as e:
            logging.error(f"Failed to initialize bot: {e}")
            return False
    
    def register_handlers(self):
        """Register all event handlers"""
        if self.bot:
            self.bot.add_event_handler(self.handle_start, events.NewMessage(pattern='/start'))
            self.bot.add_event_handler(self.handle_help, events.NewMessage(pattern='/help'))
            self.bot.add_event_handler(self.handle_accounts, events.NewMessage(pattern='/accounts'))
            self.bot.add_event_handler(self.handle_campaigns, events.NewMessage(pattern='/campaigns'))
            self.bot.add_event_handler(self.handle_stats, events.NewMessage(pattern='/stats'))
            self.bot.add_event_handler(self.handle_settings, events.NewMessage(pattern='/settings'))
            self.bot.add_event_handler(self.handle_callback, events.CallbackQuery())
            self.bot.add_event_handler(self.handle_document, events.NewMessage(func=lambda e: e.document))
            self.bot.add_event_handler(self.handle_message, events.NewMessage())
    
    async def init_account_client(self, account: Account) -> bool:
        """Initialize a client for an account"""
        if account.name in self.clients:
            return True
        
        if not os.path.exists(account.session_file):
            logging.error(f"Session file not found for {account.name}: {account.session_file}")
            return False
        
        try:
            client = TelegramClient(
                session=account.session_file,
                api_id=23633832,
                api_hash="c045f1239bbf24f22f9e21e38a0c307c"
            )
            await client.connect()
            
            if not await client.is_user_authorized():
                logging.error(f"Account {account.name} is not authorized")
                account.status = "error"
                self.db.save_account(account)
                return False
            
            # Get account info
            me = await client.get_me()
            if not account.phone_number:
                try:
                    if hasattr(me, 'phone') and me.phone:
                        account.phone_number = me.phone
                        self.db.save_account(account)
                except AttributeError:
                    # Some user types don't have phone attribute
                    pass
            
            self.clients[account.name] = client
            account.status = "active"
            self.db.save_account(account)
            logging.info(f"Account {account.name} initialized successfully")
            return True
        except Exception as e:
            logging.error(f"Failed to initialize account {account.name}: {e}")
            account.status = "error"
            account.errors_count += 1
            self.db.save_account(account)
            return False
    
    def get_available_account(self, user_id: int, exclude: Optional[Set[str]] = None) -> Optional[Account]:
        """Get an available account for sending"""
        exclude = exclude or set()
        
        available = [
            acc for acc in self.accounts.values()
            if acc.status == "active" and acc.name not in exclude
            and (not acc.flood_wait_until or datetime.now() > acc.flood_wait_until)
            and acc.user_id == user_id  # Only user's own accounts
        ]
        
        if not available:
            return None
        
        # Prefer least used account
        return min(available, key=lambda x: x.messages_sent)
    
    async def get_targets(self, client: TelegramClient, mode: str, filters: Optional[Dict] = None) -> List[Dict]:
        """Get targets based on mode and filters"""
        targets = []
        try:
            dialogs = await client.get_dialogs()
            
            for dialog in dialogs:
                entity = dialog.entity
                # Get proper title for the target
                title = 'Unknown'
                try:
                    if hasattr(dialog, 'title') and dialog.title:
                        title = dialog.title
                    elif hasattr(entity, 'first_name') and entity.first_name:
                        title = entity.first_name
                        if hasattr(entity, 'last_name') and entity.last_name:
                            title += f" {entity.last_name}"
                    elif hasattr(entity, 'username') and entity.username:
                        title = f"@{entity.username}"
                    elif hasattr(entity, 'phone') and entity.phone:
                        title = entity.phone
                except Exception:
                    title = f"User_{dialog.id}"
                
                target_info = {
                    'id': dialog.id,
                    'entity': entity,
                    'title': title
                }
                
                # Filter by mode
                if mode == 'groups':
                    if isinstance(entity, (Chat, Channel)) and (not isinstance(entity, Channel) or entity.megagroup):
                        target_info['type'] = 'group'
                        targets.append(target_info)
                elif mode == 'dms':
                    if isinstance(entity, User) and not entity.bot and not entity.is_self:
                        target_info['type'] = 'dm'
                        targets.append(target_info)
                elif mode == 'both':
                    if isinstance(entity, (Chat, Channel)) and (not isinstance(entity, Channel) or entity.megagroup):
                        target_info['type'] = 'group'
                        targets.append(target_info)
                    elif isinstance(entity, User) and not entity.bot and not entity.is_self:
                        target_info['type'] = 'dm'
                        targets.append(target_info)
            
            # Apply filters
            if filters:
                targets = self.apply_filters(targets, filters)
            
            # Remove blacklisted targets
            targets = self.remove_blacklisted(targets)
            
            # Final validation - remove targets with invalid entities or missing titles
            validated_targets = []
            for target in targets:
                if (target.get('entity') and 
                    target.get('title') != 'Unknown' and 
                    target.get('title') is not None and
                    len(target.get('title', '')) > 0):
                    validated_targets.append(target)
                else:
                    logging.debug(f"Skipping invalid target: {target.get('title', 'Unknown')}")
            
            logging.info(f"Found {len(validated_targets)} valid targets (filtered from {len(targets)} total)")
            return validated_targets
        except Exception as e:
            logging.error(f"Error getting targets: {e}")
            return []
    
    def apply_filters(self, targets: List[Dict], filters: Dict) -> List[Dict]:
        """Apply filters to target list"""
        filtered = []
        for target in targets:
            entity = target['entity']
            
            # Member count filter for groups
            if 'min_members' in filters and hasattr(entity, 'participants_count'):
                if entity.participants_count < filters['min_members']:
                    continue
            
            if 'max_members' in filters and hasattr(entity, 'participants_count'):
                if entity.participants_count > filters['max_members']:
                    continue
            
            # Keyword filters
            if 'keywords' in filters:
                title = target['title'].lower()
                if not any(keyword.lower() in title for keyword in filters['keywords']):
                    continue
            
            if 'exclude_keywords' in filters:
                title = target['title'].lower()
                if any(keyword.lower() in title for keyword in filters['exclude_keywords']):
                    continue
            
            filtered.append(target)
        
        return filtered
    
    def remove_blacklisted(self, targets: List[Dict]) -> List[Dict]:
        """Remove blacklisted targets"""
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT target_id FROM blacklist')
        blacklisted_ids = {row[0] for row in cursor.fetchall()}
        conn.close()
        
        return [t for t in targets if str(t['id']) not in blacklisted_ids]
    
    async def send_message_to_target(self, client: TelegramClient, target: Dict, message: str) -> bool:
        """Send message to a target"""
        target_title = target.get('title', 'Unknown')
        try:
            entity = target['entity']
            
            # Validate entity before sending
            if not entity:
                logging.warning(f"Invalid entity for target {target_title}")
                return False
            
            # Send message
            await client.send_message(entity, message)
            logging.info(f"Message sent to {target_title}")
            return True
            
        except errors.FloodWaitError as e:
            logging.warning(f"Flood wait: {e.seconds} seconds for target {target_title}")
            raise
        except errors.PeerFloodError:
            logging.warning(f"🚫 Peer flood error for target {target_title} - will skip this target")
            return False
        except errors.UserPrivacyRestrictedError:
            logging.info(f"🔒 Privacy restricted for target {target_title} - skipping")
            return False
        except errors.UserDeactivatedError:
            logging.info(f"User {target_title} has been deleted - skipping")
            return False
        except errors.UserDeactivatedBanError:
            logging.info(f"🚫 User {target_title} has been banned - skipping")
            return False
        except errors.InputUserDeactivatedError:
            logging.info(f"User {target_title} account deleted - skipping")
            return False
        except Exception as e:
            error_msg = str(e).lower()
            if 'deleted' in error_msg or 'deactivated' in error_msg:
                logging.info(f"User {target_title} account deleted - skipping")
            elif 'flood' in error_msg:
                logging.warning(f"🚫 Rate limited for target {target_title} - will pause")
            else:
                logging.error(f"Error sending to {target_title}: {e}")
            return False
    
    async def run_campaign(self, campaign_id: str):
        """Run a specific campaign with all available accounts"""
        if campaign_id in self.running_campaigns:
            return
        
        campaign = self.campaigns.get(campaign_id)
        if not campaign or not campaign.active:
            return
        
        self.running_campaigns.add(campaign_id)
        logging.info(f"Starting campaign: {campaign.name} (global mode)")
        
        try:
            # Get accounts for this campaign
            account_names = campaign.accounts or list(self.accounts.keys())
            campaign_accounts = [self.accounts[name] for name in account_names if name in self.accounts]
            
            if not campaign_accounts:
                logging.error(f"No accounts available for campaign {campaign.name}")
                return
            
            # Initialize clients for campaign accounts
            available_clients = {}
            for account in campaign_accounts:
                if await self.init_account_client(account):
                    available_clients[account.name] = self.clients[account.name]
            
            if not available_clients:
                logging.error(f"No clients available for campaign {campaign.name}")
                return
            
            # Get targets using first available client
            first_client = next(iter(available_clients.values()))
            targets = await self.get_targets(first_client, campaign.mode, campaign.filters)
            
            if not targets:
                logging.warning(f"No targets found for campaign {campaign.name}")
                return
            
            # Shuffle targets for better distribution
            random.shuffle(targets)
            
            sent_count = 0
            failed_count = 0
            account_rotation = 0
            
            for target in targets:
                if campaign_id not in self.running_campaigns:
                    break
                
                # Rotate through available accounts
                account_names_list = list(available_clients.keys())
                if not account_names_list:
                    break
                
                current_account_name = account_names_list[account_rotation % len(account_names_list)]
                current_client = available_clients[current_account_name]
                current_account = self.accounts[current_account_name]
                
                # Check if account is still available
                if current_account.status != "active":
                    # Remove from available clients
                    available_clients.pop(current_account_name, None)
                    if not available_clients:
                        break
                    continue
                
                # Select message
                message = random.choice(campaign.messages) if campaign.messages else "Hello!"
                
                try:
                    success = await self.send_message_to_target(current_client, target, message)
                    
                    if success:
                        sent_count += 1
                        current_account.messages_sent += 1
                        current_account.last_used = datetime.now()
                        self.stats['total_sent'] += 1
                    else:
                        failed_count += 1
                        self.stats['total_failed'] += 1
                    
                    # Log activity
                    self.db.log_activity(
                        current_account_name, campaign_id, target['id'], 
                        target['type'], success
                    )
                    
                    # Save account stats
                    self.db.save_account(current_account)
                    
                except errors.FloodWaitError as e:
                    if e.seconds > FLOOD_WAIT_TOLERANCE:
                        # Mark account as flood waited
                        current_account.status = "flood_wait"
                        current_account.flood_wait_until = datetime.now() + timedelta(seconds=e.seconds)
                        self.db.save_account(current_account)
                        available_clients.pop(current_account_name, None)
                        
                        # Log flood wait
                        self.db.log_activity(
                            current_account_name, campaign_id, target['id'], 
                            target['type'], False, f"Flood wait: {e.seconds}s"
                        )
                    else:
                        await asyncio.sleep(e.seconds)
                        continue
                
                except Exception as e:
                    failed_count += 1
                    current_account.errors_count += 1
                    self.stats['total_failed'] += 1
                    
                    # Log error
                    self.db.log_activity(
                        current_account_name, campaign_id, target['id'], 
                        target['type'], False, str(e)
                    )
                    
                    self.db.save_account(current_account)
                
                # Rotate to next account
                account_rotation += 1
                
                # Wait between messages
                await asyncio.sleep(campaign.interval)
            
            logging.info(f"Campaign {campaign.name} completed: {sent_count} sent, {failed_count} failed")
            
        except Exception as e:
            logging.error(f"Campaign {campaign.name} error: {e}")
        finally:
            self.running_campaigns.discard(campaign_id)
    
    # Event Handlers
    async def handle_start(self, event):
        # This is now a public bot - add user to authorized users
        self.add_user_to_authorized(event.sender_id)
        
        # Get user-specific data
        user_accounts = self.get_user_accounts(event.sender_id)
        user_campaigns = self.get_user_campaigns(event.sender_id)
        
        buttons = [
            [Button.inline("Dashboard", b"dashboard"), Button.inline("Help & Guide", b"help")],
            [Button.inline("My Accounts", b"accounts"), Button.inline("My Campaigns", b"campaigns")],
            [Button.inline("Statistics", b"statistics"), Button.inline("Settings", b"settings")]
        ]
        
        active_user_accounts = [a for a in user_accounts if a.status == 'active']
        active_user_campaigns = [c for c in user_campaigns if c.active]
        
        welcome_msg = f"""
**Welcome to Telegram Marketing Bot!**

The ultimate tool for growing your business on Telegram!

**Your Dashboard:**
Accounts: {len(active_user_accounts)} active / {len(user_accounts)} total
Campaigns: {len(active_user_campaigns)} active / {len(user_campaigns)} total
Uptime: {str(datetime.now() - self.stats['uptime_start']).split('.')[0]}

**Features Available:**
• Unlimited accounts & users
• Send to groups & DMs
• Real-time statistics
• Smart flood protection
• Auto-join groups
• Advanced targeting

{"Ready to start marketing!" if user_accounts and user_campaigns else "Get started by adding accounts and creating campaigns!"}
        """
        
        await event.reply(welcome_msg, buttons=buttons)
    
    async def handle_help(self, event):
        self.add_user_to_authorized(event.sender_id)
        
        help_text = """
**Telegram Marketing Bot - Complete Guide**

**What This Bot Does:**
• Manage unlimited Telegram accounts
• Create powerful marketing campaigns  
• Send messages to groups & DMs automatically
• Auto-join groups to expand your reach
• Track detailed statistics
• Smart flood protection & error handling

**Quick Start Guide:**

**Step 1: Add Your Accounts**
• Go to "My Accounts"
• Click "Add New Account"  
• Upload your .session files
• Test accounts to ensure they work

**Step 2: Join Groups (Optional)**
• Click "Join Groups" in accounts menu
• Send group links or usernames
• Bot will join them automatically
• More groups = more potential customers!

**Step 3: Create Campaigns**
• Go to "My Campaigns"
• Click "Create New Campaign"
• Add your marketing messages
• Choose target mode (Groups/DMs/Both)
• Set sending intervals

**Step 4: Activate & Start**
• Click on your campaign
• Click "Activate Campaign"
• Choose to start on all accounts or specific ones
• Monitor progress in real-time!

**Advanced Features:**
• Unlimited accounts per user
• Random delays to avoid detection  
• Account-specific campaign control
• Detailed success tracking
• Auto-skip deleted/blocked users
• Real-time campaign monitoring

**Pro Tips:**
• Start with 5-10 second intervals
• Test with small groups first
• Use multiple accounts for better reach
• Monitor statistics regularly
• Join relevant groups for your niche

**Need Help?**
Each menu has clear instructions - just follow the buttons!
        """
        
        await event.reply(help_text)
    
    async def handle_accounts(self, event):
        self.add_user_to_authorized(event.sender_id)
        await self.show_accounts_menu(event)
    
    async def handle_campaigns(self, event):
        self.add_user_to_authorized(event.sender_id)
        await self.show_campaigns_menu(event)
    
    async def handle_stats(self, event):
        self.add_user_to_authorized(event.sender_id)
        await self.show_statistics(event, event.sender_id)
    
    async def handle_settings(self, event):
        self.add_user_to_authorized(event.sender_id)
        await self.show_settings_menu(event)
    
    async def handle_document(self, event):
        if event.sender_id not in self.authorized_users:
            return
        
        user_state = self.user_state.get(event.sender_id, {})
        
        if user_state.get('action') == 'upload_session':
            await self.handle_session_upload(event)
        elif user_state.get('action') == 'import_targets':
            await self.handle_targets_import(event)
    
    async def handle_session_upload(self, event):
        try:
            # Download session file
            file_path = await event.download_media(file="./")
            
            if not file_path.endswith('.session'):
                os.remove(file_path)
                await event.reply("Invalid file. Please upload a .session file.")
                return
            
            # Generate account name
            account_name = f"account_{len(self.accounts) + 1}"
            
            # Create account object
            account = Account(name=account_name, session_file=file_path)
            
            # Test the account
            if await self.init_account_client(account):
                self.accounts[account_name] = account
                self.db.save_account(account)
                
                await event.reply(f"Account **{account_name}** added successfully!\n"
                                f"Phone: {account.phone_number or 'Unknown'}")
            else:
                os.remove(file_path)
                await event.reply("Failed to initialize account. Please check the session file.")
            
            # Clear state
            self.user_state.pop(event.sender_id, None)
            
        except Exception as e:
            logging.error(f"Session upload error: {e}")
            await event.reply(f"Error processing session file: {str(e)}")
    
    async def handle_message(self, event):
        if event.sender_id not in self.authorized_users or event.text.startswith('/'):
            return
        
        user_state = self.user_state.get(event.sender_id, {})
        action = user_state.get('action')
        
        if action == 'campaign_name':
            await self.handle_campaign_name_input(event)
        elif action == 'campaign_messages':
            await self.handle_campaign_messages_input(event)
        elif action == 'campaign_interval':
            await self.handle_campaign_interval_input(event)
        elif action == 'add_keyword_filter':
            await self.handle_keyword_filter_input(event)
        elif action == 'awaiting_group_links':
            await self.handle_group_join_input(event)
    
    async def handle_callback(self, event):
        data = event.data.decode('utf-8')
        
        try:
            if data == "dashboard":
                await self.show_dashboard(event)
            elif data == "accounts":
                await self.show_accounts_menu(event)
            elif data == "campaigns":
                await self.show_campaigns_menu(event)
            elif data == "statistics":
                await self.show_statistics(event, event.sender_id)
            elif data == "settings":
                await self.show_settings_menu(event)
            elif data == "help":
                await self.handle_help(event)
            elif data == "add_account":
                await self.initiate_account_upload(event)
            elif data == "create_campaign":
                await self.initiate_campaign_creation(event)
            elif data.startswith("account_"):
                await self.show_account_details(event, data.split("_", 1)[1])
            elif data.startswith("campaign_"):
                await self.show_campaign_details(event, data.split("_", 1)[1])
            elif data.startswith("start_campaign_"):
                campaign_id = data.split("_", 2)[2]
                await self.start_campaign(event, campaign_id)
            elif data.startswith("stop_campaign_"):
                campaign_id = data.split("_", 2)[2]
                await self.stop_campaign(event, campaign_id)
            elif data.startswith("delete_account_"):
                account_name = data.split("_", 2)[2]
                await self.delete_account(event, account_name)
            elif data.startswith("delete_campaign_"):
                campaign_id = data.split("_", 2)[2]
                await self.delete_campaign(event, campaign_id)
            elif data.startswith("start_account_campaign_"):
                parts = data.split("_", 3)
                account_name, campaign_id = parts[2], parts[3]
                await self.start_account_campaign(event, account_name, campaign_id)
            elif data.startswith("stop_account_campaign_"):
                parts = data.split("_", 3) 
                account_name, campaign_id = parts[2], parts[3]
                await self.stop_account_campaign(event, account_name, campaign_id)
            elif data.startswith("start_all_campaigns_"):
                account_name = data.split("_", 3)[3]
                await self.start_all_campaigns_for_account(event, account_name)
            elif data.startswith("view_account_campaigns_"):
                account_name = data.split("_", 3)[3]
                await self.view_account_campaigns(event, account_name)
            elif data.startswith("test_account_"):
                account_name = data.split("_", 2)[2]
                await self.test_account(event, account_name)
            elif data.startswith("activate_campaign_"):
                campaign_id = data.split("_", 2)[2]
                await self.activate_campaign(event, campaign_id)
            elif data.startswith("deactivate_campaign_"):
                campaign_id = data.split("_", 2)[2]
                await self.deactivate_campaign(event, campaign_id)
            elif data.startswith("select_account_for_campaign_"):
                campaign_id = data.split("_", 4)[4]
                await self.select_account_for_campaign(event, campaign_id)
            elif data == "join_groups":
                await self.initiate_group_join(event)
            elif data == "mode_groups":
                await self.handle_campaign_mode_selection(event, "groups")
            elif data == "mode_dms":
                await self.handle_campaign_mode_selection(event, "dms")  
            elif data == "mode_both":
                await self.handle_campaign_mode_selection(event, "both")
        except Exception as e:
            error_msg = str(e)
            if "not modified" in error_msg.lower():
                # Message content wasn't changed, ignore this error
                logging.debug(f"Message content not modified - ignoring: {e}")
                return
            else:
                logging.error(f"Callback handler error: {e}")
                try:
                    await event.answer("An error occurred. Please try again.", alert=True)
                except Exception:
                    # If we can't send the error message, just log it
                    logging.error(f"Failed to send error message to user: {e}")
    
    # UI Methods
    async def show_dashboard(self, event):
        active_accounts = len([a for a in self.accounts.values() if a.status == 'active'])
        active_campaigns = len([c for c in self.campaigns.values() if c.active])
        
        # Get recent stats
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        # Messages sent today
        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute('SELECT COUNT(*) FROM statistics WHERE DATE(timestamp) = ? AND message_sent = 1', (today,))
        messages_today = cursor.fetchone()[0]
        
        # Total messages
        cursor.execute('SELECT COUNT(*) FROM statistics WHERE message_sent = 1')
        total_messages = cursor.fetchone()[0]
        
        conn.close()
        
        dashboard_text = f"""
**System Dashboard**

**System Status**
Accounts: {active_accounts}/{len(self.accounts)}
Campaigns: {active_campaigns}/{len(self.campaigns)} active
Running: {len(self.running_campaigns)} campaigns

**Statistics**
Today: {messages_today} messages
Total: {total_messages} messages
Uptime: {datetime.now() - self.stats['uptime_start']}
        """
        
        buttons = [
            [Button.inline("Manage Accounts", b"accounts"), Button.inline("Campaigns", b"campaigns")],
            [Button.inline("Statistics", b"statistics"), Button.inline("Settings", b"settings")],
            [Button.inline("Refresh Dashboard", b"dashboard")]
        ]
        
        await event.edit(dashboard_text, buttons=buttons)
    
    async def show_accounts_menu(self, event):
        user_id = event.sender_id
        
        # Get ALL accounts first, then filter properly
        all_accounts = list(self.accounts.values())
        user_accounts = [acc for acc in all_accounts if acc.user_id == user_id or acc.user_id is None]
        
        # For accounts with None user_id, assign them to the current user if under limit
        unassigned_accounts = [acc for acc in user_accounts if acc.user_id is None]
        current_user_accounts = [acc for acc in user_accounts if acc.user_id == user_id]
        
        # Assign unassigned accounts to user if under limit
        if unassigned_accounts and len(current_user_accounts) < 2:
            for acc in unassigned_accounts[:2-len(current_user_accounts)]:
                acc.user_id = user_id
                self.db.save_account(acc)
                current_user_accounts.append(acc)
        
        user_accounts = current_user_accounts
        
        accounts_text = "**📱 Your Telegram Accounts**\n\nManage unlimited accounts for your marketing campaigns!\n\n"
        buttons = []
        
        if not user_accounts:
            accounts_text += "No accounts found.\n\n"
        else:
            for account in user_accounts:
                status_display = {
                    'active': 'ACTIVE',
                    'flood_wait': 'FLOOD_WAIT', 
                    'banned': 'BANNED',
                    'error': 'ERROR'
                }.get(account.status, 'UNKNOWN')
                
                accounts_text += f"**{account.name}** [{status_display}]\n"
                accounts_text += f"Phone: {account.phone_number or 'Unknown'}\n"
                accounts_text += f"Messages sent: {account.messages_sent}\n"
                
                if account.flood_wait_until:
                    wait_time = account.flood_wait_until - datetime.now()
                    if wait_time.total_seconds() > 0:
                        accounts_text += f"Flood wait: {int(wait_time.total_seconds()//60)} minutes\n"
                
                accounts_text += "\n"
                
                buttons.append([Button.inline(f"{account.name} [{status_display}]", f"account_{account.name}")])
        
        # Account management buttons
        buttons.append([Button.inline("➕ Add New Account", b"add_account")])
        buttons.append([Button.inline("🔗 Join Groups", b"join_groups")])
        buttons.append([Button.inline("Back to Dashboard", b"dashboard")])
        
        await event.edit(accounts_text, buttons=buttons)
    
    async def show_campaigns_menu(self, event):
        user_id = event.sender_id
        user_campaigns = self.get_user_campaigns(user_id)
        
        campaigns_text = "**🎯 Your Marketing Campaigns**\n\nCreate and manage powerful advertising campaigns!\n\n"
        buttons = []
        
        if not user_campaigns:
            campaigns_text += "No campaigns configured.\n\n"
        else:
            for campaign in user_campaigns:
                campaign_id = campaign.id
                status_icon = "🟢" if campaign.active else "🔴"
                status = "ACTIVE" if campaign.active else "INACTIVE"
                running_status = " [RUNNING]" if campaign_id in self.running_campaigns else ""
                
                campaigns_text += f"{status_icon} **{campaign.name}** [{status}]{running_status}\n"
                campaigns_text += f"Mode: {campaign.mode} | Messages: {len(campaign.messages)} | Interval: {campaign.interval}s\n\n"
                
                button_text = f"{status_icon} {campaign.name}"
                if not campaign.active:
                    button_text += " (Click to Activate)"
                elif campaign_id in self.running_campaigns:
                    button_text += " [RUNNING]"
                else:
                    button_text += " (Ready to Start)"
                
                buttons.append([Button.inline(button_text, f"campaign_{campaign_id}")])
        
        buttons.append([Button.inline("➕ Create New Campaign", b"create_campaign")])
        buttons.append([Button.inline("⬅️ Back to Dashboard", b"dashboard")])
        
        await event.edit(campaigns_text, buttons=buttons)
    
    async def show_statistics(self, event, user_id: Optional[int] = None):
        if user_id is None:
            user_id = event.sender_id
        
        # Ensure user_id is valid
        if user_id is None:
            await event.reply("Unable to identify user.")
            return
            
        user_accounts = self.get_user_accounts(user_id)
        user_account_names = [acc.name for acc in user_accounts]
        
        if not user_account_names:
            stats_text = "📈 **Your Statistics**\n\nNo accounts configured yet. Add accounts to see statistics."
            buttons = [[Button.inline("🔙 Back", b"dashboard")]]
            await event.edit(stats_text, buttons=buttons)
            return
        
        conn = sqlite3.connect(self.db.db_path)
        account_names_placeholder = ','.join('?' for _ in user_account_names)
        cursor = conn.cursor()
        
        # User's overall stats
        cursor.execute(f'SELECT COUNT(*) FROM statistics WHERE message_sent = 1 AND account_name IN ({account_names_placeholder})', user_account_names)
        total_sent = cursor.fetchone()[0]
        
        cursor.execute(f'SELECT COUNT(*) FROM statistics WHERE message_sent = 0 AND account_name IN ({account_names_placeholder})', user_account_names)
        total_failed = cursor.fetchone()[0]
        
        # User's today stats
        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute(f'SELECT COUNT(*) FROM statistics WHERE DATE(timestamp) = ? AND message_sent = 1 AND account_name IN ({account_names_placeholder})', [today] + user_account_names)
        today_sent = cursor.fetchone()[0]
        
        # User's account performance
        cursor.execute(f'''
            SELECT account_name, 
                   SUM(CASE WHEN message_sent = 1 THEN 1 ELSE 0 END) as sent,
                   SUM(CASE WHEN message_sent = 0 THEN 1 ELSE 0 END) as failed
            FROM statistics 
            WHERE account_name IN ({account_names_placeholder})
            GROUP BY account_name
            ORDER BY sent DESC
            LIMIT 5
        ''', user_account_names)
        account_stats = cursor.fetchall()
        
        conn.close()
        
        success_rate = (total_sent / (total_sent + total_failed) * 100) if (total_sent + total_failed) > 0 else 0
        
        stats_text = f"""
**Your Statistics**

**Overall Performance**
Total Sent: {total_sent}
Total Failed: {total_failed}
Success Rate: {success_rate:.1f}%
Today: {today_sent} messages

**Account Performance**
"""
        
        for account_name, sent, failed in account_stats:
            rate = (sent / (sent + failed) * 100) if (sent + failed) > 0 else 0
            stats_text += f"- {account_name}: {sent} sent, {failed} failed ({rate:.1f}%)\n"
        
        stats_text += f"\n**System Info**\nUptime: {datetime.now() - self.stats['uptime_start']}"
        
        buttons = [
            [Button.inline("Refresh", b"statistics")],
            [Button.inline("Back", b"dashboard")]
        ]
        
        await event.edit(stats_text, buttons=buttons)
    
    async def show_settings_menu(self, event):
        settings_text = """
**Settings**

**Current Configuration**
Max Flood Wait: 5 minutes
Max Accounts per User: 2
Default Interval: 5 seconds

**Available Actions**
        """
        
        buttons = [
            [Button.inline("Clear Statistics", b"clear_stats")],
            [Button.inline("Export Data", b"export_data")],
            [Button.inline("Manage Blacklist", b"blacklist")],
            [Button.inline("Back", b"dashboard")]
        ]
        
        await event.edit(settings_text, buttons=buttons)
    
    async def initiate_account_upload(self, event):
        self.user_state[event.sender_id] = {'action': 'upload_session'}
        
        instructions = """
**Add New Account**

Please upload your Telegram session file (.session).

**How to get a session file:**
1. Use a script to create a session with your account
2. Make sure the session is authorized
3. Upload the .session file here

**Note:** The session file will be tested before adding to ensure it works.
        """
        
        buttons = [[Button.inline("Cancel", b"accounts")]]
        await event.edit(instructions, buttons=buttons)
    
    async def initiate_campaign_creation(self, event):
        if not self.accounts:
            await event.answer("Add accounts first before creating campaigns!", alert=True)
            return
        
        self.user_state[event.sender_id] = {'action': 'campaign_name', 'campaign_data': {}}
        
        await event.edit(
            "**Create Campaign**\n\nStep 1: Enter campaign name:",
            buttons=[[Button.inline("Cancel", b"campaigns")]]
        )
    
    async def handle_campaign_name_input(self, event):
        user_state = self.user_state[event.sender_id]
        campaign_name = event.raw_text.strip()
        
        if len(campaign_name) < 3:
            await event.reply("Campaign name must be at least 3 characters long.")
            return
        
        campaign_id = f"campaign_{int(time.time())}"
        user_state['campaign_data'] = {
            'id': campaign_id,
            'name': campaign_name,
            'messages': [],
            'targets': [],
            'mode': 'both',
            'interval': 5,
            'active': False
        }
        
        user_state['action'] = 'campaign_messages'
        
        await event.reply(
            f"Campaign name: **{campaign_name}**\n\n"
            "Step 2: Enter your messages (one per line, or type 'done' when finished):"
        )
    
    async def handle_campaign_messages_input(self, event):
        user_state = self.user_state[event.sender_id]
        message_text = event.raw_text.strip()
        
        if message_text.lower() == 'done':
            if not user_state['campaign_data']['messages']:
                await event.reply("Add at least one message before continuing.")
                return
            
            # Show campaign mode selection
            buttons = [
                [Button.inline("Groups Only", b"mode_groups")],
                [Button.inline("DMs Only", b"mode_dms")],
                [Button.inline("Both", b"mode_both")]
            ]
            
            messages_count = len(user_state['campaign_data']['messages'])
            await event.reply(
                f"Added {messages_count} message(s)\n\n"
                "Step 3: Select target mode:",
                buttons=buttons
            )
            user_state['action'] = 'campaign_mode'
            return
        
        user_state['campaign_data']['messages'].append(message_text)
        await event.reply(f"Message {len(user_state['campaign_data']['messages'])} added. Send another message or type 'done':")
    
    async def start_campaign(self, event, campaign_id: str):
        campaign = self.campaigns.get(campaign_id)
        if not campaign:
            await event.answer("Campaign not found!", alert=True)
            return
        
        if campaign_id in self.running_campaigns:
            await event.answer("Campaign is already running!", alert=True)
            return
        
        campaign.active = True
        self.campaigns[campaign_id] = campaign
        self.db.save_campaign(campaign)
        
        # Start campaign in background
        asyncio.create_task(self.run_campaign(campaign_id))
        
        await event.answer(f"Campaign '{campaign.name}' started!", alert=False)
        await self.show_campaign_details(event, campaign_id)
    
    async def stop_campaign(self, event, campaign_id: str):
        campaign = self.campaigns.get(campaign_id)
        if not campaign:
            await event.answer("Campaign not found!", alert=True)
            return
        
        campaign.active = False
        self.campaigns[campaign_id] = campaign
        self.db.save_campaign(campaign)
        self.running_campaigns.discard(campaign_id)
        
        await event.answer(f"Campaign '{campaign.name}' stopped!", alert=False)
        await self.show_campaign_details(event, campaign_id)
    
    async def show_campaign_details(self, event, campaign_id: str):
        campaign = self.campaigns.get(campaign_id)
        if not campaign:
            await event.edit("Campaign not found!")
            return
        
        status = "ACTIVE" if campaign.active else "INACTIVE"
        running = "RUNNING" if campaign_id in self.running_campaigns else "STOPPED"
        
        details_text = f"""
**{campaign.name}**

**Status:** {status} | {running}
**Mode:** {campaign.mode}
**Interval:** {campaign.interval} seconds
**Messages:** {len(campaign.messages)}

**Messages Preview:**
"""
        
        for i, msg in enumerate(campaign.messages[:3], 1):
            preview = msg[:50] + "..." if len(msg) > 50 else msg
            details_text += f"{i}. {preview}\n"
        
        if len(campaign.messages) > 3:
            details_text += f"... and {len(campaign.messages) - 3} more\n"
        
        buttons = []
        
        # Campaign activation/deactivation
        if not campaign.active:
            buttons.append([Button.inline("Activate Campaign", f"activate_campaign_{campaign_id}")])
        else:
            buttons.append([Button.inline("Deactivate Campaign", f"deactivate_campaign_{campaign_id}")])
        
        # Campaign start/stop (only if active)
        if campaign.active:
            if campaign_id not in self.running_campaigns:
                buttons.append([Button.inline("▶️ Start Campaign (All Accounts)", f"start_campaign_{campaign_id}")])
            else:
                buttons.append([Button.inline("⏸️ Stop Campaign (All Accounts)", f"stop_campaign_{campaign_id}")])
        
        # Show account-specific options if campaign is active
        if campaign.active:
            user_accounts = self.get_user_accounts(event.sender_id)
            active_accounts = [acc for acc in user_accounts if acc.status == 'active']
            if active_accounts:
                buttons.append([Button.inline("Start on Specific Account", f"select_account_for_campaign_{campaign_id}")])
        
        buttons.extend([
            [Button.inline("✏️ Edit", f"edit_campaign_{campaign_id}"), 
             Button.inline("🗑️ Delete", f"delete_campaign_{campaign_id}")],
            [Button.inline("Back", b"campaigns")]
        ])
        
        await event.edit(details_text, buttons=buttons)
    
    async def show_account_details(self, event, account_name: str):
        account = self.accounts.get(account_name)
        if not account:
            await event.edit("Account not found!")
            return
        
        status_info = {
            'active': 'ACTIVE',
            'flood_wait': 'FLOOD_WAIT',
            'banned': 'BANNED', 
            'error': 'ERROR'
        }
        
        status = status_info.get(account.status, '⚪ Unknown')
        
        details_text = f"""
**{account_name}**

**Status:** {status}
**Phone:** {account.phone_number or 'Unknown'}
**Messages Sent:** {account.messages_sent}
**Errors:** {account.errors_count}
**Last Used:** {account.last_used.strftime('%Y-%m-%d %H:%M') if account.last_used else 'Never'}
"""
        
        if account.flood_wait_until:
            wait_time = account.flood_wait_until - datetime.now()
            if wait_time.total_seconds() > 0:
                details_text += f"**Flood Wait Until:** {account.flood_wait_until.strftime('%H:%M:%S')}\n"
        
        # Get user's campaigns for this account
        user_campaigns = self.get_user_campaigns(event.sender_id)
        active_campaigns = [c for c in user_campaigns if c.active]
        
        buttons = [
            [Button.inline("Test Account", f"test_account_{account_name}")],
        ]
        
        # Add campaign start buttons if there are active campaigns
        if active_campaigns:
            buttons.append([Button.inline("Start All Campaigns", f"start_all_campaigns_{account_name}")])
            for campaign in active_campaigns[:3]:  # Show first 3 campaigns
                running_status = " ⏸️" if campaign.id in self.running_campaigns else " ▶️"
                button_text = f"{campaign.name[:15]}...{running_status}" if len(campaign.name) > 15 else f"{campaign.name}{running_status}"
                if campaign.id in self.running_campaigns:
                    buttons.append([Button.inline(f"⏸️ Stop: {button_text}", f"stop_account_campaign_{account_name}_{campaign.id}")])
                else:
                    buttons.append([Button.inline(f"▶️ Start: {button_text}", f"start_account_campaign_{account_name}_{campaign.id}")])
            
            if len(active_campaigns) > 3:
                buttons.append([Button.inline(f"View All ({len(active_campaigns)} campaigns)", f"view_account_campaigns_{account_name}")])
        else:
            buttons.append([Button.inline("No Active Campaigns", b"campaigns")])
        
        buttons.extend([
            [Button.inline("Delete Account", f"delete_account_{account_name}")],
            [Button.inline("Back", b"accounts")]
        ])
        
        await event.edit(details_text, buttons=buttons)
    
    async def delete_account(self, event, account_name: str):
        """Delete an account"""
        user_id = event.sender_id
        account = self.accounts.get(account_name)
        
        if not account or account.user_id != user_id:
            await event.answer("Account not found or not owned by you!", alert=True)
            return
        
        # Remove from memory and database
        del self.accounts[account_name]
        if account_name in self.clients:
            client = self.clients[account_name]
            if client and hasattr(client, 'disconnect'):
                try:
                    await client.disconnect()
                except Exception:
                    pass
            del self.clients[account_name]
        
        # Remove from database
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM accounts WHERE name = ?', (account_name,))
        conn.commit()
        conn.close()
        
        await event.answer(f"Account '{account_name}' deleted!", alert=False)
        await self.show_accounts_menu(event)
    
    async def delete_campaign(self, event, campaign_id: str):
        """Delete a campaign"""
        user_id = event.sender_id
        campaign = self.campaigns.get(campaign_id)
        
        if not campaign or campaign.user_id != user_id:
            await event.answer("Campaign not found or not owned by you!", alert=True)
            return
        
        # Stop if running
        if campaign_id in self.running_campaigns:
            self.running_campaigns.discard(campaign_id)
        
        # Remove from memory and database
        del self.campaigns[campaign_id]
        
        # Remove from database
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM campaigns WHERE id = ?', (campaign_id,))
        conn.commit()
        conn.close()
        
        await event.answer(f"Campaign '{campaign.name}' deleted!", alert=False)
        await self.show_campaigns_menu(event)
    
    async def handle_targets_import(self, event):
        """Handle targets import from file"""
        await event.reply("📁 Send me a text file with target usernames/IDs (one per line)")
        self.user_state[event.sender_id] = {'action': 'awaiting_targets_file'}
    
    async def handle_campaign_interval_input(self, event):
        """Handle campaign interval input"""
        try:
            interval = int(event.raw_text.strip())
            if interval < 1:
                await event.reply("Interval must be at least 1 second.")
                return
            
            user_state = self.user_state[event.sender_id]
            user_state['campaign_data']['interval'] = interval
            
            await event.reply(f"Interval set to {interval} seconds.\n\nCampaign created successfully!")
            
            # Create and save campaign
            campaign_data = user_state['campaign_data']
            campaign = Campaign(
                id=f"camp_{int(time.time())}_{event.sender_id}",
                name=campaign_data['name'],
                messages=campaign_data['messages'],
                targets=[],
                mode=campaign_data['mode'],
                interval=interval,
                user_id=event.sender_id
            )
            
            self.campaigns[campaign.id] = campaign
            self.db.save_campaign(campaign)
            
            del self.user_state[event.sender_id]
            await self.show_campaigns_menu(event)
            
        except ValueError:
            await event.reply("Please enter a valid number for the interval.")
    
    async def handle_keyword_filter_input(self, event):
        """Handle keyword filter input"""
        keywords = [kw.strip() for kw in event.raw_text.split(',') if kw.strip()]
        
        if not keywords:
            await event.reply("Please enter at least one keyword.")
            return
        
        user_state = self.user_state[event.sender_id]
        if 'filters' not in user_state['campaign_data']:
            user_state['campaign_data']['filters'] = {}
        
        user_state['campaign_data']['filters']['keywords'] = keywords
        
        await event.reply(f"Added keyword filters: {', '.join(keywords)}\n\nNow enter the message sending interval (seconds):")
        user_state['action'] = 'campaign_interval'
    
    async def handle_campaign_mode_selection(self, event, mode: str):
        """Handle target mode selection during campaign creation"""
        user_id = event.sender_id
        if user_id not in self.user_state or self.user_state[user_id].get('action') != 'campaign_mode':
            await event.answer("Campaign creation session expired. Please start over.", alert=True)
            return
        
        # Update campaign data with selected mode
        user_state = self.user_state[user_id]
        user_state['campaign_data']['mode'] = mode
        
        mode_names = {
            'groups': 'Groups Only',
            'dms': 'DMs Only', 
            'both': 'Both Groups & DMs'
        }
        
        await event.edit(
            f"Target mode set to: {mode_names[mode]}\n\n"
            f"Step 4: Enter message sending interval (seconds):\n"
            f"Recommended: 5-10 seconds to avoid rate limiting",
            buttons=[[Button.inline("Cancel", b"campaigns")]]
        )
        
        user_state['action'] = 'campaign_interval'
    
    async def initiate_group_join(self, event):
        """Initiate group join process"""
        user_id = event.sender_id
        user_accounts = self.get_user_accounts(user_id)
        active_accounts = [acc for acc in user_accounts if acc.status == 'active']
        
        if not active_accounts:
            await event.answer("Add active accounts first before joining groups!", alert=True)
            return
        
        self.user_state[user_id] = {'action': 'awaiting_group_links', 'groups': []}
        
        instructions = """
**Join Telegram Groups**

Send me group links or usernames to join them automatically!

**Accepted formats:**
• https://t.me/groupname
• https://t.me/joinchat/xxxxx
• @groupname
• groupname

**Instructions:**
1. Send one link/username per message
2. Type 'done' when finished
3. I'll join all groups with your accounts

**Tips:**
• Use this to expand your reach
• Join relevant groups for your niche
• More groups = more potential customers

Send your first group link/username:
        """
        
        buttons = [[Button.inline("Cancel", b"accounts")]]
        await event.edit(instructions, buttons=buttons)
    
    async def handle_group_join_input(self, event):
        """Handle group link/username input"""
        user_id = event.sender_id
        user_state = self.user_state.get(user_id, {})
        
        if user_state.get('action') != 'awaiting_group_links':
            return
        
        text = event.raw_text.strip()
        
        if text.lower() == 'done':
            if not user_state.get('groups'):
                await event.reply("Add at least one group before finishing.")
                return
            
            await self.process_group_joins(event, user_state['groups'])
            return
        
        # Process the group link/username
        group_identifier = self.process_group_identifier(text)
        if group_identifier:
            user_state['groups'].append(group_identifier)
            await event.reply(f"Added: {group_identifier}\n\nSend another group link/username or type 'done':")
        else:
            await event.reply("Invalid format. Please send a valid group link or username.")
    
    def _clean_username(self, group: str) -> str:
        """Clean username for Telegram API calls"""
        if group.startswith('https://t.me/'):
            return group.replace('https://t.me/', '')
        elif group.startswith('@'):
            return group[1:]
        else:
            return group
    
    def process_group_identifier(self, text: str) -> str:
        """Process and validate group identifier"""
        text = text.strip()
        
        # Handle t.me links
        if 't.me/' in text:
            if '/joinchat/' in text:
                return text  # Join chat link
            elif text.startswith(('https://t.me/', 'http://t.me/')):
                return text
            else:
                return f"https://t.me/{text.split('/')[-1]}"
        
        # Handle username format
        if text.startswith('@'):
            return text[1:]  # Remove @ symbol
        
        # Plain username - validate alphanumeric + underscore
        if text.replace('_', '').isalnum():
            return text
        
        return None
    
    async def process_group_joins(self, event, groups: List[str]):
        """Process joining multiple groups"""
        user_id = event.sender_id
        user_accounts = self.get_user_accounts(user_id)
        active_accounts = [acc for acc in user_accounts if acc.status == 'active']
        
        if not active_accounts:
            await event.reply("No active accounts available for joining groups!")
            return
        
        # Clear user state
        del self.user_state[user_id]
        
        total_groups = len(groups)
        successful_joins = 0
        failed_joins = 0
        
        status_msg = await event.reply(f"**Starting to join {total_groups} groups...**\n\nPlease wait...")
        
        for i, group in enumerate(groups):
            try:
                # Use first available account for joining
                account = active_accounts[0]
                if not await self.init_account_client(account):
                    continue
                
                client = self.clients[account.name]
                
                # Try to join the group
                try:
                    if '/joinchat/' in group:
                        # Join via invite link
                        from telethon.tl.functions.messages import ImportChatInviteRequest
                        invite_hash = group.split('/joinchat/')[1]
                        await client(ImportChatInviteRequest(invite_hash))
                    else:
                        # Join via username or public link
                        from telethon.tl.functions.channels import JoinChannelRequest
                        
                        # Clean username - remove @ and https://t.me/ if present
                        if group.startswith('https://t.me/'):
                            username = group.replace('https://t.me/', '')
                        elif group.startswith('@'):
                            username = group[1:]
                        else:
                            username = group
                        
                        await client(JoinChannelRequest(username))
                    
                    successful_joins += 1
                    logging.info(f"Successfully joined group: {group}")
                    
                except Exception as join_error:
                    failed_joins += 1
                    error_msg = str(join_error).lower()
                    if 'already' in error_msg:
                        logging.info(f"ℹ️ Already member of group: {group}")
                        successful_joins += 1  # Count as success
                        failed_joins -= 1
                    elif 'flood' in error_msg:
                        logging.warning(f"Flood wait for group: {group}")
                        await asyncio.sleep(60)  # Wait 1 minute for flood
                    else:
                        logging.error(f"Failed to join group {group}: {join_error}")
                
                # Update status every few groups
                if (i + 1) % 3 == 0 or i == len(groups) - 1:
                    try:
                        await status_msg.edit(f"""
**Group Join Progress**

Successful: {successful_joins}
Failed: {failed_joins}
Progress: {i + 1}/{total_groups}

{"Completed!" if i == len(groups) - 1 else "Processing..."}
                        """)
                    except:
                        pass
                
                # Wait between joins to avoid rate limiting
                if i < len(groups) - 1:
                    await asyncio.sleep(random.uniform(5, 10))
                    
            except Exception as e:
                logging.error(f"Error processing group {group}: {e}")
                failed_joins += 1
        
        # Final status message
        final_msg = f"""
**Group Join Complete!**

Successfully joined: {successful_joins} groups
Failed to join: {failed_joins} groups
Success rate: {(successful_joins/(successful_joins+failed_joins)*100):.1f}%

**What's next?**
• Create campaigns to message these groups
• Check your account details for group targets
• Start marketing to your new audience!
        """
        
        buttons = [
            [Button.inline("Create Campaign", b"create_campaign")],
            [Button.inline("View Accounts", b"accounts")]
        ]
        
        try:
            await status_msg.edit(final_msg, buttons=buttons)
        except:
            await event.reply(final_msg, buttons=buttons)
    
    async def activate_campaign(self, event, campaign_id: str):
        """Activate a campaign"""
        user_id = event.sender_id
        campaign = self.campaigns.get(campaign_id)
        
        if not campaign or campaign.user_id != user_id:
            await event.answer("Campaign not found or not owned by you!", alert=True)
            return
        
        campaign.active = True
        self.campaigns[campaign_id] = campaign
        self.db.save_campaign(campaign)
        
        await event.answer(f"Campaign '{campaign.name}' activated!", alert=False)
        await self.show_campaign_details(event, campaign_id)
    
    async def deactivate_campaign(self, event, campaign_id: str):
        """Deactivate a campaign"""
        user_id = event.sender_id
        campaign = self.campaigns.get(campaign_id)
        
        if not campaign or campaign.user_id != user_id:
            await event.answer("Campaign not found or not owned by you!", alert=True)
            return
        
        # Stop campaign if running
        if campaign_id in self.running_campaigns:
            self.running_campaigns.discard(campaign_id)
        
        # Stop all account-specific campaigns
        user_accounts = self.get_user_accounts(user_id)
        for account in user_accounts:
            running_key = f"{account.name}_{campaign_id}"
            self.running_campaigns.discard(running_key)
        
        campaign.active = False
        self.campaigns[campaign_id] = campaign
        self.db.save_campaign(campaign)
        
        await event.answer(f"Campaign '{campaign.name}' deactivated!", alert=False)
        await self.show_campaign_details(event, campaign_id)
    
    async def select_account_for_campaign(self, event, campaign_id: str):
        """Show account selection for starting a specific campaign"""
        user_id = event.sender_id
        campaign = self.campaigns.get(campaign_id)
        
        if not campaign or campaign.user_id != user_id:
            await event.answer("Campaign not found or not owned by you!", alert=True)
            return
        
        if not campaign.active:
            await event.answer("Campaign must be activated first!", alert=True)
            return
        
        user_accounts = self.get_user_accounts(user_id)
        active_accounts = [acc for acc in user_accounts if acc.status == 'active']
        
        if not active_accounts:
            await event.answer("No active accounts found!", alert=True)
            return
        
        accounts_text = f"**Select Account for Campaign: {campaign.name}**\n\n"
        accounts_text += "Choose which account to start this campaign on:\n\n"
        
        buttons = []
        
        for account in active_accounts:
            running_key = f"{account.name}_{campaign_id}"
            status_icon = "⏸️" if running_key in self.running_campaigns else "▶️"
            status_text = "RUNNING" if running_key in self.running_campaigns else "STOPPED"
            
            accounts_text += f"**{account.name}** [{status_text}]\n"
            accounts_text += f"Phone: {account.phone_number or 'Unknown'}\n"
            accounts_text += f"Messages sent: {account.messages_sent}\n\n"
            
            if running_key in self.running_campaigns:
                buttons.append([Button.inline(f"⏸️ Stop on {account.name}", f"stop_account_campaign_{account.name}_{campaign_id}")])
            else:
                buttons.append([Button.inline(f"▶️ Start on {account.name}", f"start_account_campaign_{account.name}_{campaign_id}")])
        
        buttons.append([Button.inline("Back to Campaign", f"campaign_{campaign_id}")])
        
        await event.edit(accounts_text, buttons=buttons)
    
    async def start_account_campaign(self, event, account_name: str, campaign_id: str):
        """Start a specific campaign with a specific account"""
        user_id = event.sender_id
        account = self.accounts.get(account_name)
        campaign = self.campaigns.get(campaign_id)
        
        if not account or account.user_id != user_id:
            await event.answer("Account not found or not owned by you!", alert=True)
            return
            
        if not campaign or campaign.user_id != user_id:
            await event.answer("Campaign not found or not owned by you!", alert=True)
            return
        
        if account.status != "active":
            await event.answer(f"Account {account_name} is not active (Status: {account.status})", alert=True)
            return
            
        # Create a unique running key for this account-campaign combination
        running_key = f"{account_name}_{campaign_id}"
        
        if running_key in self.running_campaigns:
            await event.answer("This campaign is already running on this account!", alert=True)
            return
        
        # Start the campaign with only this account
        self.running_campaigns.add(running_key)
        campaign.active = True
        self.campaigns[campaign_id] = campaign
        self.db.save_campaign(campaign)
        
        # Start campaign in background with specific account
        asyncio.create_task(self.run_account_campaign(campaign_id, account_name))
        
        await event.answer(f"▶️ Campaign '{campaign.name}' started on account '{account_name}'!", alert=False)
        await self.show_account_details(event, account_name)
    
    async def stop_account_campaign(self, event, account_name: str, campaign_id: str):
        """Stop a specific campaign on a specific account"""
        user_id = event.sender_id
        account = self.accounts.get(account_name)
        campaign = self.campaigns.get(campaign_id)
        
        if not account or account.user_id != user_id:
            await event.answer("Account not found or not owned by you!", alert=True)
            return
            
        if not campaign or campaign.user_id != user_id:
            await event.answer("Campaign not found or not owned by you!", alert=True)
            return
        
        # Create a unique running key for this account-campaign combination
        running_key = f"{account_name}_{campaign_id}"
        
        if running_key not in self.running_campaigns:
            await event.answer("This campaign is not running on this account!", alert=True)
            return
        
        self.running_campaigns.discard(running_key)
        
        await event.answer(f"⏸️ Campaign '{campaign.name}' stopped on account '{account_name}'!", alert=False)
        await self.show_account_details(event, account_name)
    
    async def start_all_campaigns_for_account(self, event, account_name: str):
        """Start all active campaigns for a specific account"""
        user_id = event.sender_id
        account = self.accounts.get(account_name)
        
        if not account or account.user_id != user_id:
            await event.answer("Account not found or not owned by you!", alert=True)
            return
        
        if account.status != "active":
            await event.answer(f"Account {account_name} is not active (Status: {account.status})", alert=True)
            return
        
        user_campaigns = self.get_user_campaigns(user_id)
        active_campaigns = [c for c in user_campaigns if c.active]
        
        if not active_campaigns:
            await event.answer("No active campaigns found!", alert=True)
            return
        
        started_count = 0
        for campaign in active_campaigns:
            running_key = f"{account_name}_{campaign.id}"
            if running_key not in self.running_campaigns:
                self.running_campaigns.add(running_key)
                asyncio.create_task(self.run_account_campaign(campaign.id, account_name))
                started_count += 1
        
        if started_count > 0:
            await event.answer(f"Started {started_count} campaigns on account '{account_name}'!", alert=False)
        else:
            await event.answer("All campaigns are already running on this account!", alert=True)
        
        await self.show_account_details(event, account_name)
    
    async def view_account_campaigns(self, event, account_name: str):
        """Show all campaigns for a specific account"""
        user_id = event.sender_id
        account = self.accounts.get(account_name)
        
        if not account or account.user_id != user_id:
            await event.answer("Account not found or not owned by you!", alert=True)
            return
        
        user_campaigns = self.get_user_campaigns(user_id)
        active_campaigns = [c for c in user_campaigns if c.active]
        
        campaigns_text = f"**Campaigns for Account: {account_name}**\n\n"
        buttons = []
        
        if not active_campaigns:
            campaigns_text += "No active campaigns found."
            buttons.append([Button.inline("Create Campaign", b"create_campaign")])
        else:
            for campaign in active_campaigns:
                running_key = f"{account_name}_{campaign.id}"
                running_status = "RUNNING" if running_key in self.running_campaigns else "STOPPED"
                
                campaigns_text += f"**{campaign.name}** [{running_status}]\n"
                campaigns_text += f"Mode: {campaign.mode} | Interval: {campaign.interval}s\n"
                campaigns_text += f"Messages: {len(campaign.messages)}\n\n"
                
                if running_key in self.running_campaigns:
                    buttons.append([Button.inline(f"⏸️ Stop: {campaign.name}", f"stop_account_campaign_{account_name}_{campaign.id}")])
                else:
                    buttons.append([Button.inline(f"▶️ Start: {campaign.name}", f"start_account_campaign_{account_name}_{campaign.id}")])
        
        buttons.append([Button.inline("Back to Account", f"account_{account_name}")])
        
        await event.edit(campaigns_text, buttons=buttons)
    
    async def test_account(self, event, account_name: str):
        """Test an account by sending a test message to the user"""
        user_id = event.sender_id
        account = self.accounts.get(account_name)
        
        if not account or account.user_id != user_id:
            await event.answer("Account not found or not owned by you!", alert=True)
            return
        
        if account.status != "active":
            await event.answer(f"Account {account_name} is not active (Status: {account.status})", alert=True)
            return
        
        # Initialize the account client if needed
        if not await self.init_account_client(account):
            await event.answer("Failed to initialize account client!", alert=True)
            return
        
        try:
            client = self.clients[account_name]
            # Send a test message to the user who requested the test
            await client.send_message(user_id, f"Test message from account: {account_name}\nAccount is working properly!")
            await event.answer(f"Test successful! Check your messages.", alert=False)
        except Exception as e:
            logging.error(f"Test failed for account {account_name}: {e}")
            await event.answer(f"Test failed: {str(e)}", alert=True)
    
    async def run_account_campaign(self, campaign_id: str, account_name: str):
        """Run a campaign with a specific account"""
        running_key = f"{account_name}_{campaign_id}"
        
        if running_key not in self.running_campaigns:
            return
        
        campaign = self.campaigns.get(campaign_id)
        account = self.accounts.get(account_name)
        
        if not campaign or not account:
            self.running_campaigns.discard(running_key)
            return
        
        logging.info(f"Starting account-specific campaign: {campaign.name} on {account_name}")
        
        try:
            # Initialize client for this account
            if not await self.init_account_client(account):
                logging.error(f"Failed to initialize client for account {account_name}")
                self.running_campaigns.discard(running_key)
                return
            
            client = self.clients[account_name]
            
            # Get targets using this client
            targets = await self.get_targets(client, campaign.mode, campaign.filters)
            
            if not targets:
                logging.warning(f"No targets found for campaign {campaign.name} on account {account_name}")
                self.running_campaigns.discard(running_key)
                return
            
            # Shuffle targets for better distribution
            random.shuffle(targets)
            
            sent_count = 0
            failed_count = 0
            
            for target in targets:
                if running_key not in self.running_campaigns:
                    break
                
                # Check if account is still available
                if account.status != "active":
                    logging.warning(f"Account {account_name} is no longer active, stopping campaign")
                    break
                
                # Select message
                message = random.choice(campaign.messages) if campaign.messages else "Hello!"
                
                try:
                    success = await self.send_message_to_target(client, target, message)
                    
                    if success:
                        sent_count += 1
                        account.messages_sent += 1
                        account.last_used = datetime.now()
                        self.stats['total_sent'] += 1
                    else:
                        failed_count += 1
                        self.stats['total_failed'] += 1
                    
                    # Log activity
                    self.db.log_activity(
                        account_name, campaign_id, target['id'], 
                        target['type'], success
                    )
                    
                    # Save account stats
                    self.db.save_account(account)
                    
                except errors.FloodWaitError as e:
                    if e.seconds > FLOOD_WAIT_TOLERANCE:
                        # Mark account as flood waited
                        account.status = "flood_wait"
                        account.flood_wait_until = datetime.now() + timedelta(seconds=e.seconds)
                        self.db.save_account(account)
                        
                        # Log flood wait
                        self.db.log_activity(
                            account_name, campaign_id, target['id'], 
                            target['type'], False, f"Flood wait: {e.seconds}s"
                        )
                        
                        logging.warning(f"Account {account_name} hit flood wait: {e.seconds}s")
                        break
                    else:
                        # Wait for the flood wait period
                        logging.info(f"Short flood wait for {account_name}: {e.seconds}s")
                        await asyncio.sleep(e.seconds)
                        continue
                
                except Exception as e:
                    logging.error(f"Error in account campaign {account_name}: {e}")
                    failed_count += 1
                    
                    # Log error
                    self.db.log_activity(
                        account_name, campaign_id, target['id'], 
                        target['type'], False, str(e)
                    )
                    
                    # Check for critical errors
                    if "banned" in str(e).lower() or "terminated" in str(e).lower():
                        account.status = "banned"
                        self.db.save_account(account)
                        logging.error(f"Account {account_name} appears to be banned")
                        break
                
                # Enhanced wait between messages to prevent rate limiting
                base_interval = max(campaign.interval, 3)  # Minimum 3 seconds
                # Add random variation to avoid detection
                random_delay = random.uniform(0.5, 2.0)
                total_delay = base_interval + random_delay
                
                logging.debug(f"⏱️ Waiting {total_delay:.1f} seconds before next message")
                await asyncio.sleep(total_delay)
            
            logging.info(f"Campaign {campaign.name} on {account_name} completed: {sent_count} sent, {failed_count} failed")
            
        except Exception as e:
            logging.error(f"Fatal error in account campaign {account_name}: {e}")
            account.status = "error"
            account.errors_count += 1
            self.db.save_account(account)
        
        finally:
            # Remove from running campaigns
            self.running_campaigns.discard(running_key)
            logging.info(f"Account campaign {account_name}_{campaign_id} finished")

async def main():
    bot = TelegramAdBot()
    
    if not bot.bot_token:
        logging.error("BOT_TOKEN environment variable not set")
        return
    
    try:
        success = await bot.init_bot()
        if not success:
            logging.error("Failed to initialize bot")
            return
        
        logging.info(f"Bot started successfully with {len(bot.accounts)} accounts")
        logging.info("Public bot mode - all users can access")
        
        if bot.bot:
            await bot.bot.run_until_disconnected()
        
    except KeyboardInterrupt:
        logging.info("Bot stopped by user")
    except Exception as e:
        logging.error(f"Bot error: {e}")
        traceback.print_exc()
    finally:
        # Close all client connections
        if bot.clients:
            for client in bot.clients.values():
                if client:
                    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())