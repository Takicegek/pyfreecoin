#!/usr/bin/env python

MAX_MSG_SIZE = 1024*1024
PORT         = 64720 # hex:fcd0

CHAIN_RECALC_INTERVAL = 2016

MAGIC_PING_RETRIES = 5
MAGIC_PING_TIMEOUT = 30

CTYPE_REJECT     = 0
CTYPE_GETHIGHEST = 1
CTYPE_GETCHAIN   = 2
CTYPE_GETTXS     = 3
CTYPE_INV        = 4
CTYPE_GETDATA    = 5
CTYPE_BLOCK      = 6
CTYPE_TX         = 7
CTYPE_PEER       = 8
CTYPE_ALERT      = 9
CTYPE_PING       = 10
CTYPE_PONG       = 11

DTYPE_BLOCK = 0
DTYPE_TX    = 1
DTYPE_PEER  = 2

OTYPE_WARNUSER    = 0 # Display a warning to the user, visible at all times.
OTYPE_FORCEUPDATE = 1 # Do not allow user to do anything until the client is updated.

ERR_BAD_VERSION = 1
ERR_BAD_CTYPE = 2
ERR_BAD_DTYPE = 3
ERR_BAD_ATYPE = 4
ERR_MESSAGE_MALFORMED = 5

ERR_BLOCK_BLACKLISTED = 6
ERR_BLOCK_INVALID     = 7

ERR_TX_INVALID        = 8