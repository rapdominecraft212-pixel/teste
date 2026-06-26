#!/usr/bin/env python3
"""
Threaded SSH Proxy for Git
Uses threads instead of non-blocking I/O for reliable data transfer.
"""
import paramiko
import sys
import os
import threading
import fcntl
import time

SSH_KEY_PATH = "/home/z/.ssh/id_ed25519_glm"

def forward_stdin_to_channel(channel, stop_event):
    """Forward data from stdin to SSH channel."""
    try:
        while not stop_event.is_set():
            try:
                data = os.read(sys.stdin.fileno(), 65536)
                if not data:
                    break
                channel.sendall(data)
            except (BlockingIOError, OSError):
                stop_event.wait(0.01)
            except Exception:
                break
    except Exception:
        pass
    finally:
        try:
            channel.shutdown_write()
        except:
            pass

def forward_channel_to_stdout(channel, stop_event):
    """Forward data from SSH channel to stdout."""
    try:
        while not stop_event.is_set():
            if channel.recv_ready():
                data = channel.recv(65536)
                if not data:
                    break
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
            elif channel.exit_status_ready():
                break
            else:
                stop_event.wait(0.01)
    except Exception:
        pass

def main():
    args = sys.argv[1:]
    hostname = None
    username = "git"
    port = 22
    git_command = None
    
    i = 0
    while i < len(args):
        if args[i] == "-o" and i + 1 < len(args):
            i += 2
            continue
        elif args[i] == "-p" and i + 1 < len(args):
            port = int(args[i + 1])
            i += 2
            continue
        elif "@" in args[i] and hostname is None:
            parts = args[i].split("@", 1)
            username = parts[0]
            hostname = parts[1]
        elif git_command is None:
            git_command = args[i]
        i += 1
    
    if not hostname or not git_command:
        sys.exit(1)
    
    try:
        key = paramiko.Ed25519Key.from_private_key_file(SSH_KEY_PATH)
    except Exception as e:
        print(f"SSH key error: {e}", file=sys.stderr)
        sys.exit(1)
    
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname, port=port, username=username, pkey=key, 
                      timeout=15, allow_agent=False, look_for_keys=False)
        
        transport = client.get_transport()
        channel = transport.open_session()
        channel.exec_command(git_command)
        
        try:
            flags = fcntl.fcntl(sys.stdin.fileno(), fcntl.F_GETFL)
            fcntl.fcntl(sys.stdin.fileno(), fcntl.F_SETFL, flags | os.O_NONBLOCK)
        except:
            pass
        
        stop_event = threading.Event()
        
        stdin_thread = threading.Thread(
            target=forward_stdin_to_channel, 
            args=(channel, stop_event),
            daemon=True
        )
        stdout_thread = threading.Thread(
            target=forward_channel_to_stdout, 
            args=(channel, stop_event),
            daemon=True
        )
        
        stdin_thread.start()
        stdout_thread.start()
        
        while not channel.exit_status_ready():
            stop_event.wait(0.1)
        
        time.sleep(0.2)
        stop_event.set()
        
        exit_status = channel.recv_exit_status()
        channel.close()
        client.close()
        sys.exit(exit_status)
        
    except Exception as e:
        print(f"SSH error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
