import os,sys
import atexit
import socket
import socketserver
import threading
from binascii import hexlify
from time import sleep

import freecoin as fc
from freecoin.net import *

# P2P network connection
class Network():
    def __init__(self,port=PORT):
        self.server = _Server(self, ("",port), _ServerHandler)
        self.thread = None
        self.peers = []
        self.mempool = {}
        atexit.register(self.shutdown)

    def serve(self):
        if self.thread is not None and self.thread.is_alive():
            fc.logger.error("Attempted to start server which was already running")
            return
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()
        
        # Initial peer
        if len(self.peers) == 0:
            known = Peer.from_file_list()
            for peer_t in known:
                peer = self.connect(*peer_t)
                if peer is None:
                    Peer.delete_file_static(*peer_t)
        
        # Custodians
        self.chain_custodian()
        self.peer_custodian()

    def shutdown(self):
        for peer in self.peers:
            peer.shutdown()
        self.peers = []
        if self.thread is not None and self.thread.is_alive():
            self.server.shutdown()
    
    def chain_custodian(self):
        # Try to finish incomplete chains
        highest = fc.chain.get_highest_head(chained_only=False)
        if not highest.chained:
            for peer in self.peers:
                peer.send_getchain(highest.get_lowest_parent_hash(), 255)
        
        thread = threading.Timer(5, self.chain_custodian)
        thread.daemon = True
        thread.start()
    
    def peer_custodian(self):
        # Try to get latest chained block
        for peer in self.peers:
            peer.send_gethighest()
        # Try to stay stable
        if len(self.peers) < 8:
            for peer in self.peers:
                peer.send_getpeers()
        if len(self.peers) == 0:
            fc.logger.warn("Not connected to any peers!")
        
        thread = threading.Timer(120, self.peer_custodian)
        thread.daemon = True
        thread.start()

    def connect(self, addr, port=PORT):
        ### SERVER PEER CREATION ###
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((addr,port))
            sock.settimeout(None)
        except socket.error as e:
            fc.logger.error("net: failed to connect to server %s:%d [%s]" % (addr,port,e))
            Peer.delete_file_static(addr,port)
            return None
        peer = Peer(self, sock, addr, port, is_server=True)
        peer.to_file()
        thread = threading.Thread(target=peer.handle)
        thread.daemon = True
        thread.start()
        self.peers.append(peer)
        return peer
    
    def is_stable(self):
        return len(self.peers) >= 4

class _ServerHandler(socketserver.BaseRequestHandler):
    def handle(self):
        ### CLIENT PEER CREATION ###
        peer = Peer(self.server.network, self.request, self.client_address[0], 0, is_server=False)
        self.server.network.peers.append(peer)
        peer.handle()

# Server
class _Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    def __init__(self, network, inet_addr, handler):
        super().__init__(inet_addr, handler)
        self.network = network

# Peer
class Peer:
    def __init__(self, network, sock, addr, port, is_server):
        self.network = network
        self.sock = sock
        self.addr = addr
        self.port = port
        self.is_server = is_server
        self.pong_count = 0
        self.queue = [] #TODO

        self.receivers = {
            0:self.recv_reject,
            1:self.recv_gethighest,
            2:self.recv_getchain,
            3:self.recv_gettxs,
            4:self.recv_getpeers,
            5:self.recv_inv,
            6:self.recv_getdata,
            7:self.recv_block,
            8:self.recv_tx,
            9:self.recv_peer,
            10:self.recv_alert,
            11:self.recv_ping,
            12:self.recv_pong
        }

    def shutdown(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except socket.error:
            pass
        self.sock.close()
        if self in self.network.peers:
            self.network.peers.remove(self)
            
    def to_file(self):
        self.to_file_static(self.addr,self.port)
    
    @staticmethod
    def to_file_static(addr,port):
        with open(fc.FILE_KNOWNPEERS,'r+') as f:
            entry = (addr, str(port))
            known = [tuple(s.strip().split(":")) for s in f.read().split("\n") if ":" in s]
            if not entry in known:
                known.append(entry)
                f.seek(0)
                for peer_t in known:
                    if len(peer_t) == 2:
                        f.write(peer_t[0] + ":" + peer_t[1] + "\n")
                f.truncate()
    
    def delete_file(self):
        delete_file_static(self.addr,self.port)
    
    @staticmethod
    def delete_file_static(addr,port):
        entry = addr + ":" + str(port)
        with open(fc.FILE_KNOWNPEERS,'r+') as f:
            lines = [s.strip() for s in f.read().split("\n")]
            if entry in lines:
                lines.remove(entry)
                f.seek(0)
                for line in lines:
                    f.write(line + "\n")
                f.truncate()
            
    @staticmethod
    def from_file_list():
        with open(fc.FILE_KNOWNPEERS) as f:
            peers_strs = [tuple(s.strip().split(":")) for s in f.read().split("\n")]
            return [(e[0],int(e[1])) for e in peers_strs if len(e)==2 and e[0] != '']
    
    def to_bytes(self):
        addr = self.addr.encode("ascii")
        bytes = b""
        bytes += self.port.to_bytes(2, byteorder='big')
        bytes += len(addr).to_bytes(1, byteorder='big')
        bytes += addr
        return bytes
    
    @staticmethod
    def from_bytes(bytes):
        try:
            port = int.from_bytes(bytes[:2], byteorder='big')
            size = int.from_bytes(bytes[2:3], byteorder='big')
            addr = bytes[3:3+size].decode("ascii")
            return (addr,port)
        except:
            return None
        

    def handle(self):
        fc.logger.verbose("net: New peer: %s:%d" % (self.addr,self.port))
        self.send_ping()                      # Version exchange
        if not self.network.is_stable:
            self.send_getpeers()              # Peer exchange
        self.send_gethighest()                # Block exchange
        
        try:
            while True:
                header = self.sock.recv(SIZE_HEADER)
                if header == b"":
                    break
                size = int.from_bytes(header[:4], byteorder='big')
                vers = int.from_bytes(header[4:6], byteorder='big')
                if vers != fc._VERSION_:
                    self.send_reject(ERR_BAD_VERSION)
                    break
                ctype = int.from_bytes(header[6:7], byteorder='big')
                if ctype not in self.receivers:
                    self.send_reject(ERR_BAD_CTYPE)
                    continue
                
                data = self.sock.recv(size)
                self.receivers[ctype](data)
        except socket.error as e:
            fc.logger.error("net: error during recv: " + str(e))
        self.shutdown()
        fc.logger.error("net: Peer lost (%s:%d)" % (self.addr, self.port))#TODO

    def recv_reject(self, data):
        if len(data) == 0:
            fc.logger.verbose("net: recieve <reject>")
        else:
            e_type = data[0]
            e_str = data[1:]
            fc.logger.warn("net: recieve <reject> [%d] \"%s\"" % (e_type, e_str.decode("ascii")))
    
    def recv_gethighest(self, data):
        fc.logger.verbose("net: recieve <gethighest>")
        highest = fc.chain.get_highest_hash(chained_only=True)
        if highest is not None:
            self.send_inv(DTYPE_BLOCK, [highest])

    def recv_getchain(self, data):
        fc.logger.verbose("net: recieve <getchain>")
        if len(data) < 33:
            self.send_reject(ERR_MESSAGE_MALFORMED, "getchain too short")
            return
        
        start = data[0:32]
        count = data[32]
        
        block = fc.Block.from_file(start)
        i = 0
        while block is not None and i<count:
            self.send_block(block)
            block = fc.Block.from_file(block.prev_hash)
            i += 1

    def recv_gettxs(self, data):
        fc.logger.verbose("net: recieve <gettxs>")
        self.send_inv(DTYPE_TX, [tx.compute_hash() for tx in self.network.mempool.keys()])
    
    def recv_getpeers(self, data):
        for peer in self.network.peers:
            if peer.is_server and peer is not self:
                self.send_peer(peer)
    
    def recv_inv(self, data):
        fc.logger.verbose("net: recieve <inv>")
        if len(data) < 34:
            self.send_reject(ERR_MESSAGE_MALFORMED, "inv impossibly short")
            return
        dtype = data[0]
        count = data[1]
        hashl = data[2:]
        if len(hashl) > 32*255:
            self.send_reject(ERR_MESSAGE_MALFORMED, "inv list too large")
            return
        if len(hashl) % 32 != 0:
            self.send_reject(ERR_MESSAGE_MALFORMED, "invalid inv list size")
            return
        hashes = fc.util.divide(hashl, 32)
        
        if dtype == DTYPE_BLOCK:
            #blacklisted = lambda h: fc.is_block_blacklisted(h) TODO
            dirname   = fc.DIR_BLOCKS
        elif dtype == DTYPE_TX:
            #blacklisted = lambda h: False # TODO: Txs should not be blacklisted
            dirname   = fc.DIR_TX
        else:
            self.send_reject(ERR_BAD_DTYPE, "inv")
            return
        
        needed = []
        for hash in hashes:
            #if blacklisted(hash):
            #    continue
            if hexlify(hash).decode("ascii") not in os.listdir(dirname):
                needed.append(hash)
        
        if len(needed) > 0:
            self.send_getdata(dtype, needed)
        

    def recv_getdata(self, data):
        fc.logger.verbose("net: recieve <getdata>")
        if len(data) == 0:
            self.send_reject(ERR_MESSAGE_MALFORMED, info="getdata without dtype")
            return
        elif len(data) < 34:
            self.send_reject(ERR_MESSAGE_MALFORMED, info="getdata impossibly short")
            return
        dtype = data[0]
        count = data[1]
        hashl = data[2:]
        
        if len(hashl) > 32*255:
            self.send_reject(ERR_MESSAGE_MALFORMED, "getdata list too large")
            return
        if (len(hashl) % 32) != 0:
            self.send_reject(ERR_MESSAGE_MALFORMED, "invalid getdata list size")
            return
        hashes = fc.util.divide(hashl,32)
        
        if dtype == DTYPE_BLOCK:
            for hash in hashes:
                block = fc.Block.from_file(hash)
                if block is not None:
                    self.send_block(block)
        elif dtype == DTYPE_TX:
            for hash in hashes:
                tx = fc.Tx.from_file(hash)
                if tx is not None:
                    self.send_tx(tx)
        else:
            self.send_reject(ERR_BAD_DTYPE, "getdata")
            return
        
    def recv_block(self, data):
        fc.logger.verbose("net: recieve <block>")
        block = fc.Block.from_bytes(data)
        if block is None:
            self.send_reject(ERR_MESSAGE_MALFORMED, "failed to parse block")
            return
        hash = block.compute_hash()
        #if fc.is_block_blacklisted(hash):
        #    self.send_reject(ERR_BLOCK_BLACKLISTED, hexlify(hash).decode())
        #    return
        if not block.is_pseudo_valid():
            self.send_reject(ERR_BLOCK_INVALID, hexlify(hash).decode())
            return
        else:
            fc.chain.enchain(block)
        
    def recv_tx(self, data):
        fc.logger.verbose("net: recieve <tx>")
        tx = Tx.from_bytes(data)
        if tx is None:
            self.send_reject(ERR_MESSAGE_MALFORMED, "failed to parse transaction")
            return
        hash = tx.compute_hash()
        if not tx.is_pseudo_valid():
            self.send_reject(ERR_TX_INVALID, hexlify(hash).decode())
            return
        if hash not in self.network.mempool:
            self.network.mempool[hash] = tx

    def recv_peer(self, data):
        fc.logger.verbose("net: recieve <peer>")
        peer_t = Peer.from_bytes(data)
        if peer_t is None:
            return
        if not self.network.is_stable():
            peer = self.network.connect(*peer_t)

    def recv_alert(self, data):
        fc.logger.error("net: recieve <peer> [doing nothing, please implement functionality]")
        #TODO implement this and change to verbose

    def recv_ping(self, data):
        fc.logger.verbose("net: recieve <ping>")
        self.send_pong()

    def recv_pong(self, data):
        fc.logger.verbose("net: recieve <pong>")
        self.pong_count +=1

    # Send methods
    
    def send_reject(self, etype, info=""):
        self.send(CTYPE_REJECT, etype.to_bytes(1, byteorder='big') + info.encode("ascii"))

    def send_gethighest(self):
        self.send(CTYPE_GETHIGHEST, b"")
        
    def send_getchain(self, block_hash, count):
        self.send(CTYPE_GETCHAIN, block_hash + count.to_bytes(1, byteorder='big'))

    def send_gettxs(self):
        self.send(CTYPE_GETTXS, b"")
    
    def send_getpeers(self):
        self.send(CTYPE_GETPEERS, b"")

    def send_inv(self, dtype, ids):
        self.send(CTYPE_INV, dtype.to_bytes(1, byteorder='big') + len(ids).to_bytes(1, byteorder='big') + b"".join([id.encode("ascii") if type(id) is str else id for id in ids]))

    def send_getdata(self, dtype, ids):
        self.send(CTYPE_GETDATA, dtype.to_bytes(1, byteorder='big') + len(ids).to_bytes(1, byteorder='big') + b"".join([id.encode("ascii") if type(id) is str else id for id in ids]))

    def send_block(self, block):
        bytes = block.to_bytes()
        self.send(CTYPE_BLOCK, bytes)

    def send_tx(self, tx):
        bytes = tx.to_bytes()
        self.send(CTYPE_TX, bytes)

    def send_peer(self, peer):
        bytes = peer.to_bytes()
        self.send(CTYPE_PEER, bytes)

    def send_alert(self, atype, otype, time, msg):
        with open(os.path.join(DIR_STORAGE, "admin_secret")) as f:
            adminkey = ecdsa.SigningKey.generate().from_pem(f.read())
        doc = atype.to_bytes(1, byteorder='big')  + \
              otype.to_bytes(1, byteorder='big')  + \
              int(time()).to_bytes(4, byteorder='big') + \
              msg.encode("ascii")
        self.send(CTYPE_ALERT, len(msg).to_bytes(1, byteorder='big') + adminkey.sign(doc) + doc)

    def send_ping(self):
        thread = threading.Thread(target=self.send_magic_ping)
        thread.daemon = True
        thread.start()

    # Threaded
    def send_magic_ping(self):
        initial = self.pong_count
        for i in range(MAGIC_PING_RETRIES):
            if not self.send(CTYPE_PING, b""):
                return
            sleep(MAGIC_PING_TIMEOUT)
            if self.pong_count > initial:
                return
        fc.logger.verbose("net: peer unresponsive %s:%d" % (self.addr, self.port))
        self.shutdown()
    
    def send_pong(self):
        self.send(CTYPE_PONG, b"")
    

    def send(self, ctype, body):
        bytes = b""
        bytes += len(body).to_bytes(4, byteorder='big')
        bytes += fc._VERSION_.to_bytes(2, byteorder='big')
        bytes += ctype.to_bytes(1, byteorder='big')
        bytes += body
        try:
            self.sock.sendall(bytes)
            return True
        except socket.error as e:
            self.shutdown()
            fc.logger.error("net: Peer death during send: " + str(e))
            return False