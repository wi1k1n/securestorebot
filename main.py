#!/usr/bin/env python
# -*- coding: utf-8 -*-
# This program is dedicated to the public domain under the CC0 license.

"""
SecureStore
"""

import logging

from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (Updater, CommandHandler, MessageHandler, Filters,
						  ConversationHandler)

import db_handler as dbh
from api_token import TOKEN
from crypto import *
from util import *

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
					level=logging.INFO)

logger = logging.getLogger(__name__)

UNAUTHORIZED, IDLE, ASK_PASSWORD, ACTION_PASSWORD, ASK_ENCODE = range(5)

BTN_PWD_STRONGER = 'Create stronger'
BTN_PWD_LEAVEWEAK = 'Leave weak'
BTN_PWD_TRYAGAIN = 'Try again'
BTN_PWD_STARTOVER = 'Start over'
BTN_ENCRYPT = 'Encrypt'
BTN_DECRYPT = 'Decrypt'
BTN_PWD_CHANGE = 'Change password'
BTN_FINISH = 'Finish'
BTN_PWD_NEW = 'Create new password'

MODE_PWD_SET, MODE_PWD_TEST, MODE_PWD_AUTHORIZED = range(3)

markup_idle = [[BTN_ENCRYPT, BTN_DECRYPT], [BTN_PWD_CHANGE]]

conv_handler = None

# Checks and actions needed to be performed on each atomic signal received from user
# 1. Leave groups/channels and stay only in private chats
# 2. Check authorization state and update authorization timer
def every_signal_checks(upd, ctx):
	if not upd.message.chat.type == upd.message.chat.PRIVATE:
		logger.warning("Added to group #{0}! Leaving...".format(upd.message.chat_id))
		upd.message.bot.leave_chat(upd.message.chat_id)
	elif check_authorization(ctx):
		update_authorization_timer(upd, ctx)

# Checks if authorization expired and returns True - is still authorized, False - o\w
def check_authorization(ctx):
	return 'authorized' in ctx.user_data \
			and ctx.user_data['authorized'] is not None \
			and timestamp_now() - ctx.user_data['authorized'] <= 30

# Is called when user is inactive for specified time. Shows corresponding msg and TODO: changes conversation state
def authorization_alarm(alarm_ctx):
	global conv_handler
	job = alarm_ctx.job
	upd = job.context['upd']
	ctx = job.context['ctx']
	chat_id = upd.message.chat_id

	ctx.bot.send_message(chat_id, text='You were inactive for 30 seconds, so now you need to prove your identity.\n'
									   'Enter the password, please.', reply_markup=ReplyKeyboardRemove())
	ctx.user_data.pop('authorized_job', None)
	update_authorization_timer(upd, ctx, unauthorize=True)
	ctx.user_data['password_mode'] = MODE_PWD_TEST
	logger.info('authorization_alarm')

	conv_handler.update_state(ASK_PASSWORD, conv_handler._get_key(upd))

# Called each time, when user makes action. Sets up new alarm instead of prev and updates authorization timestamp
def update_authorization_timer(upd, ctx, unauthorize=False):
	ctx.user_data['authorized'] = None if unauthorize else timestamp_now()
	logger.info('update_authorization_timer: authorized={0}   unauthorize={1}'.format(ctx.user_data['authorized'], unauthorize))
	if not unauthorize:
		if 'authorized_job' in ctx.user_data:
			logger.info('update_authorization_timer: job_removed'.format(ctx.user_data['authorized'], unauthorize))
			ctx.user_data['authorized_job'].schedule_removal()
		ctx.user_data['authorized_job'] = ctx.job_queue.run_once(authorization_alarm, 30, context={'upd': upd, 'ctx': ctx})
		logger.info('update_authorization_timer: job_created'.format(ctx.user_data['authorized'], unauthorize))

# Entry point
def start(upd, ctx):
	# ctx.user_data contains 3 password fields:
	# 	'password'		- contains hash-sum of real password
	#	'password_mode'	- takes one of MODE_PWD_SET/MODE_PWD_TEST/MODE_PWD_AUTHORIZED and indicates current state
	#	'authorized'	- either None/integer, correspondingly indicating absence of authorization or the time of last authorization

	chat_id = upd.message.chat_id
	# Add new chat_id to DB
	dbh.create_chat_if_not_exist(chat_id)
	pwd = dbh.get_password(chat_id)

	# if pwd is None: # Seems to be redundant
	# 	logger.warning('Chat #{0} could not be found. Creating new entry.'.format(chat_id))

	# If password is already set
	if isinstance(pwd, str) and len(pwd) > 0:
		ctx.user_data['password'] = pwd.encode()

		# If authorization still valid
		if check_authorization(ctx):
			ctx.user_data['password_mode'] = MODE_PWD_AUTHORIZED
			upd.message.reply_text(
				"Hi again! My name is Charles. You can trust me all your secrets and nobody will ever have known about them except you.\n"
				"Use menu buttons to start securely storing your data.",
				reply_markup=ReplyKeyboardMarkup([[BTN_ENCRYPT, BTN_DECRYPT],
												  [BTN_PWD_CHANGE]], one_time_keyboard=True))
			return IDLE
		# If no authorization or expired
		else:
			ctx.user_data['password_mode'] = MODE_PWD_TEST
			update_authorization_timer(upd, ctx, unauthorize=True)
			upd.message.reply_text(
				"Hi again! My name is Charles. You can trust me all your secrets and nobody will ever have known about them except you.\n"
				"Please, send me the password first, so I can trust you")
			return ASK_PASSWORD

	# If password need to be set
	ctx.user_data['password_mode'] = MODE_PWD_SET
	update_authorization_timer(upd, ctx, unauthorize=True)
	upd.message.reply_text(
		"Hi! My name is Charles. You can trust me all your secrets and nobody will ever have known about them except you. "
		"Please send me the password to start.\n\n"
		"Notice, there is no way recover data if the password is lost! So, please, remember it for sure!!!")
	return ASK_PASSWORD

# Checks given password
def check_password(upd, ctx):
	is_weak = is_password_weak(upd.message.text)
	hash = get_hash(upd.message.text)
	upd.message.delete()

	# Should be always False
	if 'password_mode' not in ctx.user_data:
		logger.warning('Not \'password_mode\' key in \'ctx.user_data\' dict! Considering \'password_set\' action')
		ctx.user_data['password_mode'] = MODE_PWD_SET

	# Nothing to do if already authorized
	if ctx.user_data['password_mode'] == MODE_PWD_AUTHORIZED:
		upd.message.reply_text('Authorized successfully! Use menu buttons to securely store your secrets',
							   reply_markup=ReplyKeyboardMarkup(markup_idle, one_time_keyboard=True))
		return IDLE

	# Entered password needs to be used for authorization
	if ctx.user_data['password_mode'] == MODE_PWD_TEST:
		# Entered password is correct
		if ctx.user_data['password'] == hash:
			ctx.user_data['password_mode'] = MODE_PWD_AUTHORIZED
			update_authorization_timer(upd, ctx)
			upd.message.reply_text('Successfully authorized! You can now begin securely storing your data',
								   reply_markup=ReplyKeyboardMarkup(markup_idle, one_time_keyboard=True))
			return IDLE
		# Entered password is incorrect
		else:
			update_authorization_timer(upd, ctx, unauthorize=True)
			upd.message.reply_text('Ooopsie... Entered password is incorrect! You can try again or set up a new password.\n',
								   reply_markup=ReplyKeyboardMarkup([[BTN_PWD_TRYAGAIN, BTN_PWD_NEW]], one_time_keyboard=True))
			return ACTION_PASSWORD

	# User needs to set up the password
	if ctx.user_data['password_mode'] == MODE_PWD_SET:
		# First entry of password
		if 'password' not in ctx.user_data:
			ctx.user_data['password'] = hash
			if is_weak:
				upd.message.reply_text('The password you entered is weak and does not provide enough security!\n'
									   'It is highly recommended to come up with reliable password, which satisfies:\n'
									   '- At least 8 symbols\n'
									   '- Consist of a-z, A-Z, 0-9 and/or special symbols @#$%^&+=\n\n'
									   'Do you want to change your opinion and create stronger password?',
									   reply_markup=ReplyKeyboardMarkup([[BTN_PWD_STRONGER, BTN_PWD_LEAVEWEAK]], one_time_keyboard=True))
				return ACTION_PASSWORD
			else:
				upd.message.reply_text('Please send me the password again (and remember it properly!).')
				return ASK_PASSWORD
		# Repetition of password
		else:
			if ctx.user_data['password'] != hash:
				upd.message.reply_text('Ooopsie! The passwords do not match! Please try again or create new password.',
									   reply_markup=ReplyKeyboardMarkup([[BTN_PWD_TRYAGAIN, BTN_PWD_STARTOVER]], one_time_keyboard=True))
				return ACTION_PASSWORD
			else:
				dbh.set_password(upd.message.chat_id, ctx.user_data['password'])
				ctx.user_data['password_mode'] = MODE_PWD_AUTHORIZED
				update_authorization_timer(upd, ctx)
				upd.message.reply_text('Password successfully created! You can now begin securely storing your data',
									   reply_markup=ReplyKeyboardMarkup(markup_idle, one_time_keyboard=True))
				return IDLE

# Handles user input password
def ask_password(upd, ctx):
	text = upd.message.text
	if text == BTN_PWD_STRONGER:
		ctx.user_data.pop('password', None)
		upd.message.reply_text('Very nice decision! Please send me strong password now.\n'
							   'Notice, there is no way recover data if the password is lost! So, please, remember it carefully!!!')
		return ASK_PASSWORD
	elif text == BTN_PWD_LEAVEWEAK:
		upd.message.reply_text('I\'m only offering and it is your responsibility for this decision.\n'
							   'Please repeat the password again, so I can check that you remembered it properly')
		return ASK_PASSWORD
	elif text == BTN_PWD_TRYAGAIN:
		upd.message.reply_text('Send me the password again (and remember it properly!).\n'
							   'Please check if [CAPS Lock] is off and you are using correct keyboard layout.')
		return ASK_PASSWORD
	elif text == BTN_PWD_STARTOVER:
		ctx.user_data.pop('password', None)
		upd.message.reply_text('That\'s a good idea. Create a new strong password, remember it and send it to me.')
		return ASK_PASSWORD
	elif text == BTN_PWD_NEW:
		upd.message.reply_text('Please send me the password again (and remember it properly!).')
		return ASK_PASSWORD

# Requests user to enter data
def ask_encrypt(upd, ctx):
	if upd.message.text == BTN_ENCRYPT:
		upd.message.reply_text(
			"Tell me your secret")
		return ASK_ENCODE

# Receives message, encrypts and stores into DB
def encrypt_data(upd, ctx):
	key = dbh.get_password(upd.message.chat_id)
	ln = len(upd.message.text)
	encrypted = encrypt_string(upd.message.text, key)
	upd.message.delete()

	rec = dbh.create_record(upd.message.chat_id, encrypted)

	if rec != 1:
		logger.warning('Could not save record to database. chat_id=\'{0}\', data=\'{1}\''.format(upd.message.chat_id, encrypted))
		upd.message.reply_text(
			"Error occured while saving your data. This case is already reported. Please try again later".format(ln),
			reply_markup=ReplyKeyboardMarkup(markup_idle, one_time_keyboard=True))
		return IDLE

	upd.message.reply_text(
		"Your message of length {0} has been successfully encrypted and saved".format(ln),
		reply_markup=ReplyKeyboardMarkup(markup_idle, one_time_keyboard=True))
	return IDLE


def error(update, context):
	"""Log Errors caused by Updates."""
	logger.warning('Update "%s" caused error "%s"', update, context.error)

def main():
	global conv_handler
	# Create the Updater and pass it your bot's token.
	# Make sure to set use_context=True to use the new context based callbacks
	# Post version 12 this will no longer be necessary
	updater = Updater(TOKEN, use_context=True)

	# Get the dispatcher to register handlers
	dp = updater.dispatcher

	dp.add_handler(MessageHandler(Filters.all, every_signal_checks), group=0)

	# Add conversation handler
	conv_handler = ConversationHandler(
		entry_points=[CommandHandler('start', start)],

		states={
			ASK_PASSWORD: [
				MessageHandler(Filters.text, check_password)
			],
			ACTION_PASSWORD: [
				MessageHandler(Filters.text, ask_password)
			],
			IDLE: [
				MessageHandler(Filters.regex('^{0}$'.format(BTN_ENCRYPT)), ask_encrypt)
			],
			ASK_ENCODE: [
				MessageHandler(Filters.text, encrypt_data)
			]
		},

		fallbacks=[
			# MessageHandler(Filters.regex('^Done$'), done)
		]
	)

	dp.add_handler(conv_handler, group=1)

	# log all errors
	dp.add_error_handler(error)

	# Start the Bot
	updater.start_polling()

	# Run the bot until you press Ctrl-C or the process receives SIGINT,
	# SIGTERM or SIGABRT. This should be used most of the time, since
	# start_polling() is non-blocking and will stop the bot gracefully.
	updater.idle()


if __name__ == '__main__':
	main()