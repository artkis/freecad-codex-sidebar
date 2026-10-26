"""Bounded caller for the existing FreeCAD MCP add-on's native loopback RPC."""
import argparse, json, socket, xmlrpc.client
from pathlib import Path

def call(code=None):
    socket.setdefaulttimeout(120)
    with xmlrpc.client.ServerProxy('http://127.0.0.1:9875', allow_none=True) as rpc:
        if code is None:
            return {'rpc': rpc.get_rpc_status(), 'documents': rpc.list_documents()}
        result = rpc.execute_code(code)
        if not result.get('success'):
            raise RuntimeError(json.dumps(result))
        return result

if __name__ == '__main__':
    p=argparse.ArgumentParser(); p.add_argument('--script'); p.add_argument('--code')
    a=p.parse_args()
    print(json.dumps(call(Path(a.script).read_text(encoding='utf-8-sig') if a.script else a.code), ensure_ascii=False))
