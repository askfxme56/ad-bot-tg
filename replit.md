# Overview

A Telegram advertising bot system that combines a Telegram bot interface with user account automation. The system has been modified to remove all hardcoded values and now allows users to configure the bot entirely through the Telegram interface. Users can upload their session files, provide API credentials, and configure all settings without touching the code. The bot provides functionality for message forwarding, group management, and automated message distribution with customizable intervals.

# User Preferences

Preferred communication style: Simple, everyday language.

# Recent Changes

**September 2025**: Major UI/UX improvements and feature enhancements:
- Removed 2-account limit - now supports unlimited accounts per user
- Completely redesigned user interface with modern emojis and clearer navigation
- Added group join functionality allowing users to automatically join groups via links/usernames
- Enhanced welcome dashboard with feature highlights and quick start guidance
- Improved help system with comprehensive step-by-step tutorials
- Modernized all menu buttons and added intuitive icons
- Enhanced messaging with better user feedback and progress tracking

**December 2024**: Modified the entire system to remove hardcoded credentials and implement dynamic configuration:
- Removed all hardcoded API credentials, bot tokens, admin IDs, and phone numbers
- Added `/setup` command for initial bot configuration through Telegram interface
- Implemented session file upload functionality for user authentication
- Added step-by-step setup wizard for API credentials, admin users, and phone numbers
- Made the system fully configurable without code modifications

# System Architecture

## Core Components

**Dual Client Architecture**: The system uses two separate Telegram clients - a bot client for administrative interface and control, and a user client for actual message sending operations. This separation allows the bot to provide a clean management interface while leveraging user account capabilities for broader messaging reach.

**Dynamic Configuration System**: The bot now accepts all configuration through environment variables or the `/setup` command. Users can upload session files, provide API credentials, and configure admin access entirely through the Telegram interface.

**Session-Based Authentication**: Uses Telethon's session management system to maintain persistent connections to Telegram's API. The bot uses token-based authentication while the user client uses phone number authentication with session file persistence. Session files are now uploaded by users instead of being hardcoded.

**Asynchronous Processing**: Built on asyncio foundation with Telethon's async capabilities, enabling concurrent operations for handling multiple groups, users, and message operations without blocking.

## Message Management System

**Flexible Message Storage**: Supports both single message mode and random message selection from a list. Messages can be forwarded from existing Telegram messages or composed directly, providing flexibility in content management.

**Target Management**: Maintains separate sets for group targets and individual user targets, allowing for different distribution strategies and audience segmentation.

## Configuration Management

**Environment-Based Configuration**: Designed to accept configuration through environment variables for secure deployment, with fallback to user input for development scenarios.

**Interval Control**: Configurable send intervals to prevent rate limiting and spam detection, with default safety intervals.

## Error Handling and Logging

**Structured Logging**: Implements timestamp-based logging with appropriate log levels, while suppressing verbose Telethon logs to maintain clean output.

**Graceful Error Management**: Built to handle Telegram API errors and connection issues without crashing the entire system.

# External Dependencies

## Primary Framework
- **Telethon 1.41.2**: Python library for interacting with Telegram's API, providing both bot and user client functionality

## Telegram API Integration
- **Bot API**: Used for creating the administrative interface and receiving commands
- **Client API**: Used for user account operations and message sending
- **Session Management**: Telegram session files for maintaining authenticated connections

## System Dependencies
- **Python asyncio**: For asynchronous operations and concurrent processing
- **Environment Variables**: For secure configuration management
- **File System**: For session file storage and persistence