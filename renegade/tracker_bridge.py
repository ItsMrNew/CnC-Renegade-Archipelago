"""Read-only localhost UAT feed for PopTracker. AP remains check authority."""
import asyncio
import json
import logging
import time
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

LOG=logging.getLogger('Renegade')


def snapshot(client, sequence):
    identity=client.identity or ('',-1,-1)
    value=dict(protocol=1,sequence=sequence,seed=str(identity[0]),team=identity[1],slot=identity[2],
               connected=bool(client.ready),map=None,x=None,y=None,z=None,failed=[],
               game_state=getattr(client,'tracker_game_state','waiting'))
    sample=getattr(client,'tracker_sample',None)
    if client.ready and sample and 0<=time.monotonic()-sample['sampled_at']<2:
        value.update({k:sample[k] for k in ('map','z','failed')})
        value.update({k:sample.get(k) for k in ('x','y')})
        value['game_state']='live' if sample['map'] else 'menu'
    elif value['game_state']=='live':
        value['game_state']='waiting_frame'
    return value


async def watch(client, *, ports=(65399,44444), started=None):
    """Optional service failure cannot terminate the AP/native client."""
    peers=set();sequence=0
    def packet():return json.dumps([dict(cmd='Var',name='renegade',value=snapshot(client,sequence))])
    async def connection(ws):
        await ws.send(json.dumps([dict(cmd='Info',protocol=0,name='Renegade Archipelago',
                                      version='0.7.0',features=[],slots=[])]))
        try:
            async for raw in ws:
                try:
                    commands=json.loads(raw)
                    if not isinstance(commands,list):raise ValueError('Expected command array')
                    for command in commands:
                        if not isinstance(command,dict):raise ValueError('Expected command')
                        if command.get('cmd')=='Sync':
                            peers.add(ws);await ws.send(packet())
                        else:
                            await ws.send(json.dumps([dict(cmd='ErrorReply',name=command.get('cmd',''),reason='unknown cmd')]))
                except (ValueError,TypeError):
                    await ws.close(code=1003,reason='Invalid UAT command');return
        except ConnectionClosed:pass
        finally:peers.discard(ws)
    server=None
    try:
        for port in ports:
            try:
                server=await serve(connection,'127.0.0.1',port,max_size=16384)
                if started is not None:started.set_result(server.sockets[0].getsockname()[1])
                LOG.info('PopTracker live tracking ready')
                break
            except OSError:continue
        if server is None:
            LOG.warning('PopTracker live guide unavailable: local UAT ports are in use. AP tracking remains available.')
            if started is not None:started.set_result(None)
            await client.stop.wait();return
        async with server:
            while not client.stop.is_set():
                sequence+=1
                if peers:
                    await asyncio.gather(*(ws.send(packet()) for ws in tuple(peers)),return_exceptions=True)
                    # Closed peers are removed by their own connection handler.
                await asyncio.sleep(.5)
    except asyncio.CancelledError:raise
    except Exception:
        LOG.exception('PopTracker live guide stopped; AP and game services remain active')
        if started is not None and not started.done():started.set_result(None)
        await client.stop.wait()
    finally:
        if server is not None:
            server.close();await server.wait_closed()
