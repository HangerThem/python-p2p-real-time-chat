import asyncio
import socket
import threading
from aiortc import RTCIceCandidate, RTCIceServer, RTCPeerConnection
from aiortc.sdp import candidate_from_sdp
import requests
import time

BUFFER_SIZE = 1024
BACKLOG = 5

class Peer:
    def __init__(self, host, port, name):
        self.host = host
        self.port = port
        self.name = name
        self.server_socket = self.create_socket()
        self.peers = []
        self.is_server = False
        self.peer_connection = RTCPeerConnection()

    def create_socket(self):
        return socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    async def get_ice_candidate(self):
        self.peer_connection.createDataChannel("data")
        offer = await self.peer_connection.createOffer()
        await self.peer_connection.setLocalDescription(offer)
        candidate = next(
            filter(
                lambda c: c.component == 1,
                self.peer_connection.sctp.transport.transport.iceGatherer.getLocalCandidates(),
            )
        )
        return candidate

    def start_server(self, is_restarted=False):
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(BACKLOG)
        print(f"Server started on {self.host}:{self.port}")
        self.is_server = True
        
        threading.Thread(target=self.broadcast_presence).start()
        
        if is_restarted:
            print("Notifying peers about new server...")
            self.notify_peers_about_new_server(self.host, self.port)

        while True:
            try:
                client_socket, client_address = self.server_socket.accept()
                print(f"Connection from {client_address}")
                self.peers.append(client_socket)
                threading.Thread(target=self.handle_client, args=(client_socket,)).start()
            except OSError:
                break  # Exit the loop if the socket is closed

    def connect_to_peer(self, host, port):
        print(f"Connecting to {host}:{port}")
        client_socket = self.create_socket()
        client_socket.connect((host, port))
        self.peers.append(client_socket)
        threading.Thread(target=self.handle_client, args=(client_socket,)).start()

    def handle_client(self, client_socket):
        while True:
            try:
                message = client_socket.recv(BUFFER_SIZE).decode()
                if not message:
                    break
                print(message)
                if message.startswith("SERVER_SHUTDOWN"):
                    self.start_server(is_restarted=True)
                    break
                if message.startswith("RECONNECT"):
                    host, port = self.parse_host_and_port(message)
                    self.connect_to_peer(host, port)
                    break
                if message.startswith("SERVER_RESTART"):
                    print("Server restarted. Connecting to new server...")
                    self.host, self.port = self.parse_host_and_port(message)
                    self.connect_to_peer(self.host, self.port)
                    break
                else:
                    self.broadcast(message, client_socket)
            except:
                break

        # Connection to the peer has been lost
        self.peers.remove(client_socket)
        client_socket.close()

    def parse_host_and_port(self, message):
        host_port = message.split()[1]
        host, port = host_port.split(":")
        return host, int(port)

    def broadcast(self, message, source_socket):
        for peer in self.peers:
            if peer != source_socket:
                try:
                    peer.send(message.encode())
                except:
                    self.peers.remove(peer)
                    peer.close()

    def send_message(self, message):
        for peer in self.peers:
            try:
                peer.send((self.name + ": " + message).encode())
            except:
                self.peers.remove(peer)
                peer.close()

    def notify_new_server(self):
        if self.peers:
            first_peer = self.peers[0]
            new_server_info = f"SERVER_SHUTDOWN {self.host}:{self.port}"
            try:
                first_peer.send(new_server_info.encode())
                self.peers.remove(first_peer)
                self.notify_peers_about_new_server(first_peer.getsockname()[0], self.port)
                first_peer.close()
            except:
                pass
            
    def notify_peers_about_new_server(self, host, port):
        for peer in self.peers:
            try:
                peer.send(f"SERVER_RESTART {host}:{port}".encode())
            except:
                self.peers.remove(peer)
                peer.close()
                
    def get_public_ip(self):
        try:
            response = requests.get('https://api.ipify.org')
            return response.text
        except:
            print("Could not fetch public IP.")
            return None

    def broadcast_presence(self):
        broadcast_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        broadcast_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while True:
            broadcast_socket.sendto(f"PEER {self.host}:{self.port}".encode(), ('<broadcast>', self.port))
            time.sleep(5)  # Delay between broadcasts

    def listen_for_broadcasts(self):
        broadcast_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        broadcast_socket.bind(('', self.port))
        while True:
            message, address = broadcast_socket.recvfrom(1024)
            if message.startswith(b"PEER"):
                host, port = message.split()[1].decode().split(":")
                if (host, port) not in [(p.getsockname()[0], p.getsockname()[1]) for p in self.peers]:
                    self.connect_to_peer(host, int(port))

if __name__ == "__main__":
    host = socket.gethostbyname(socket.gethostname())
    port = 12345
    
    name = input("Enter your name: ")

    peer = Peer(host, port, name)

    loop = asyncio.get_event_loop()
    candidate = loop.run_until_complete(peer.get_ice_candidate())

    choice = input("Do you want to (1) start a server, (2) connect to a server, or (3) listen for peers? Enter 1, 2, or 3: ")

    if choice == "1":
        threading.Thread(target=peer.start_server).start()
        public_ip = peer.get_public_ip()
        if public_ip:
            print(f"Your public IP is: {public_ip}")
    elif choice == "2":
        server_host = input("Enter server host: ")
        server_port = int(input("Enter server port: "))
        peer.connect_to_peer(server_host, server_port)
    elif choice == "3":
        threading.Thread(target=peer.listen_for_broadcasts).start()

    try:
        while True:
            message = input()
            peer.send_message(message)
    except KeyboardInterrupt:
        if peer.is_server:
            peer.notify_new_server()
        peer.server_socket.close()
        for p in peer.peers:
            p.close()