# Advanced Telegram AdBot

A comprehensive multi-account Telegram advertising bot with advanced features for mass messaging campaigns.

## 🚀 Features

### Multi-Account Support
- Support for up to 10 Telegram accounts
- Automatic account rotation to avoid flood limits
- Smart flood protection with account switching
- Account health monitoring and auto-recovery

### Campaign Management  
- Create unlimited campaigns with custom messages
- Target groups, DMs, or both
- Message templates with randomization
- Flexible scheduling and timing controls
- Advanced target filtering (keywords, member count)

### Smart Protection
- Intelligent flood wait handling
- Account rotation during restrictions
- Automatic blacklist management
- Error recovery and retry mechanisms

### Analytics & Monitoring
- Comprehensive statistics dashboard
- Real-time campaign monitoring
- Account performance tracking
- Export capabilities for data analysis

### User Interface
- Intuitive Telegram bot interface
- Real-time status updates
- Easy campaign creation wizard
- Account management tools

## 📋 Setup

### 1. Environment Variables
Create a `.env` file with:
```env
BOT_TOKEN=your_bot_token_from_botfather
ADMIN_IDS=your_user_id,other_admin_id
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Run the Bot
```bash
python telegram_adbot.py
```

### 4. Add Accounts
- Start the bot with `/start`
- Go to Account Management
- Upload your `.session` files
- Bot will automatically validate accounts

### 5. Create Campaigns
- Access Campaign Management
- Follow the creation wizard
- Set messages, targets, and intervals
- Start your campaigns

## 🎯 Usage

### Commands
- `/start` - Main dashboard
- `/accounts` - Manage accounts  
- `/campaigns` - Manage campaigns
- `/stats` - View statistics
- `/settings` - Bot configuration
- `/help` - Help information

### Account Management
- Upload session files via bot interface
- Monitor account status and health
- View performance metrics
- Automatic flood protection

### Campaign Creation
1. Name your campaign
2. Add message templates
3. Select target mode (Groups/DMs/Both)
4. Set sending intervals
5. Configure filters (optional)
6. Start the campaign

### Advanced Features
- **Smart Rotation**: Automatically switches between accounts
- **Flood Protection**: Handles Telegram limits gracefully  
- **Target Filters**: Filter by keywords, member count, etc.
- **Blacklisting**: Automatic and manual target exclusion
- **Statistics**: Detailed performance analytics
- **Recovery**: Auto-retry failed messages
- **Scheduling**: Campaign timing controls

## 📊 Statistics

The bot tracks comprehensive statistics:
- Messages sent/failed per account
- Campaign performance metrics
- Success rates and error analysis
- Target response tracking
- System uptime and health

## 🛡️ Safety Features

- Respects Telegram's rate limits
- Implements smart delays between messages
- Automatic account switching on restrictions
- Error logging and monitoring
- Graceful handling of banned accounts

## 🔧 Advanced Configuration

### Message Templates
- Support for multiple message variants
- Random selection for natural variation
- Emoji and formatting support
- Link and media inclusion

### Target Filtering
- Keyword inclusion/exclusion
- Member count ranges
- Group type filtering
- Custom blacklist management

### Account Rotation
- Load balancing across accounts
- Health-based account selection
- Automatic flood wait handling
- Error recovery mechanisms

## 📝 Database

The bot uses SQLite to store:
- Account information and status
- Campaign configurations  
- Activity logs and statistics
- Blacklist and filters
- Performance metrics

## 🚨 Important Notes

- Use responsibly and follow Telegram's Terms of Service
- Monitor account health regularly
- Set appropriate sending intervals
- Respect user privacy and consent
- Keep session files secure

## 🆘 Troubleshooting

### Common Issues
1. **Account not working**: Check session file validity
2. **Flood waits**: Reduce sending frequency
3. **No targets found**: Check filter settings
4. **Campaign not starting**: Verify account availability

### Error Recovery
- Bot automatically handles most errors
- Failed accounts are temporarily disabled
- Campaigns continue with available accounts
- Detailed logging for issue diagnosis

## 📈 Performance Tips

1. **Use multiple accounts** to increase throughput
2. **Set reasonable intervals** (5-60 seconds recommended)
3. **Monitor flood waits** and adjust accordingly
4. **Filter targets carefully** to improve success rates
5. **Regular maintenance** of accounts and campaigns

## 🔒 Security

- Session files are stored locally and encrypted
- Environment variables for sensitive data
- No credentials in code or logs
- Secure database storage
- Admin-only access controls

---

**Version**: 2.0.0  
**License**: Private Use Only  
**Support**: Contact administrator