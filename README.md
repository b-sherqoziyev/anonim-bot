# Anonymous Telegram Bot

A professional, modular Telegram bot that enables anonymous messaging between users. Users can share unique links to receive anonymous messages, and administrators have access to a comprehensive admin panel for managing users and content.

## Features

### User Features
- **Anonymous Messaging**: Share a unique link to receive anonymous messages
- **Multiple Media Support**: Send and receive text, photos, videos, voice messages, and documents
- **Reply Functionality**: Recipients can reply to anonymous messages
- **User-Friendly Interface**: Simple commands and intuitive flow

### Admin Features
- **Admin Panel**: Comprehensive dashboard for bot management
- **User Management**: 
  - Search users by ID
  - View recent users with pagination
  - View detailed user information
- **Mute System**: Temporarily mute users with custom duration and reason
- **Broadcast Messages**: Send messages to all users with progress tracking
- **Statistics**: View total, monthly, and daily user statistics
- **Message Logging**: All media messages are logged to a designated channel

## Project Structure

```
anonim-bot/
├── main.py                 # Bot entry point and dispatcher setup
├── config.py               # Configuration and environment variables
├── db.py                   # Database pool and helper functions
├── utils.py                # Utility functions
├── states.py               # FSM states for conversation flows
├── handlers/
│   ├── __init__.py
│   ├── user_handlers.py    # User command handlers (/start, /help, questions)
│   └── admin_handlers.py   # Admin command handlers (panel, mute, broadcast, etc.)
├── requirements.txt        # Python dependencies
├── Dockerfile              # Docker configuration
├── Procfile                # Heroku deployment configuration
└── README.md               # This file
```

## Installation

### Prerequisites
- Python 3.11 or higher
- PostgreSQL database
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))

### Setup

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd anonim-bot
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Create a `.env` file** in the project root:
   ```env
   BOT_TOKEN=your_bot_token_here
   DATABASE_URL=postgresql://user:password@localhost:5432/dbname
   LOG_CHANNEL_ID=your_log_channel_id
   ADMIN_URL=https://t.me/your_admin_username
   ```

5. **Run the bot**
   ```bash
   python main.py
   ```

## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `BOT_TOKEN` | Telegram bot token from BotFather | Yes |
| `DATABASE_URL` | PostgreSQL connection string | Yes |
| `LOG_CHANNEL_ID` | Telegram channel ID for logging media messages | Yes |
| `ADMIN_URL` | Link to admin contact (used in help message) | Yes |

### Database Setup

The bot automatically creates the following tables on first run:
- `users` - User information and tokens
- `muted_users` - Muted users with expiration times
- `message_log` - Message history log

### Admin Setup

To make a user an admin, update the database:
```sql
UPDATE users SET is_admin = TRUE WHERE user_id = <telegram_user_id>;
```

## Usage

### User Commands

- `/start` - Start the bot and get your personal anonymous link
- `/help` - Show help information

### Admin Commands

- `/admin` - Open the admin panel

### Admin Panel Features

1. **Broadcast**: Send messages to all users
2. **Statistics**: View user statistics (total, monthly, daily)
3. **Users**: 
   - Search users by ID
   - View recent users
   - Mute/unmute users

## Deployment

### Docker Deployment

1. **Build the image**
   ```bash
   docker build -t anonim-bot .
   ```

2. **Run the container**
   ```bash
   docker run -d --env-file .env --name anonim-bot anonim-bot
   ```

### Heroku Deployment

1. **Create a Heroku app**
   ```bash
   heroku create your-app-name
   ```

2. **Set environment variables**
   ```bash
   heroku config:set BOT_TOKEN=your_token
   heroku config:set DATABASE_URL=your_database_url
   heroku config:set LOG_CHANNEL_ID=your_channel_id
   heroku config:set ADMIN_URL=your_admin_url
   ```

3. **Deploy**
   ```bash
   git push heroku main
   ```

## Technology Stack

- **Python 3.11+**
- **aiogram 3.7.0** - Modern Telegram Bot API framework
- **asyncpg** - Fast PostgreSQL async driver
- **python-dotenv** - Environment variable management

## Code Architecture

The project follows a modular architecture:

- **Separation of Concerns**: Each module has a single responsibility
- **Clean Code**: Well-documented with docstrings
- **Maintainability**: Easy to extend and modify
- **Scalability**: Efficient database connection pooling

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is open source and available under the MIT License.

## Support

For support, contact the admin via the link provided in the `/help` command or open an issue in the repository.

---

**Note**: Make sure to keep your `.env` file secure and never commit it to version control. Add it to your `.gitignore` file.


