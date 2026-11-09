"""机身摄像头（camera.py）：拆 MJPEG 流里的帧；不同屏幕方向把画面转正的滤镜。"""
from thinksmart_client import camera


def test_split_jpegs_keeps_the_partial_tail():
    a, b = b'\xff\xd8AAA\xff\xd9', b'\xff\xd8BB\xff\xd9'
    frames, rest = camera.split_jpegs(b'junk' + a + b + b'\xff\xd8CC')
    assert frames == [a, b] and rest == b'\xff\xd8CC'
    frames, rest = camera.split_jpegs(rest + b'C\xff\xd9')
    assert frames == [b'\xff\xd8CCC\xff\xd9'] and rest == b''


def test_rotation_filters():
    graph = lambda r: camera.ffmpeg_args(r)[camera.ffmpeg_args(r).index('-filter_complex') + 1]
    assert graph('90').startswith('[0:v]fps=8,split=2')                   # 横屏放在支架上：本来就是正的
    assert graph('normal').startswith('[0:v]fps=8,transpose=1,split=2')   # 竖屏：顺时针转 90°
    assert graph('180').startswith('[0:v]fps=8,transpose=2,')
    assert graph('270').startswith('[0:v]fps=8,hflip,vflip,')
    assert 'hflip[pv]' in graph('normal') and '[st]' in graph('normal')   # 预览镜像，整图不镜像
    assert 'scale=-2:640' in graph('normal') and 'scale=-2:360' in graph('90')


def test_preview_waits_for_the_first_frame(monkeypatch):
    """摄像头刚打开、第一帧还没出来时，预览要等它，不能马上结束（曾经一连上就断开）。"""
    import asyncio
    from types import SimpleNamespace

    async def run():
        cam = camera.Camera(lambda: 'normal')
        cam.seq = 41                                     # 上一次打开留下的计数
        proc = SimpleNamespace(returncode=None, terminate=lambda: None)

        async def start():
            cam.proc = proc
        monkeypatch.setattr(cam, '_start', start)
        gen = cam.frames()
        first = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0.05)
        assert not first.done()                          # 还没有画面：在等
        async with cam.changed:
            cam.frame, cam.seq = b'\xff\xd8x\xff\xd9', cam.seq + 1
            cam.changed.notify_all()
        assert await asyncio.wait_for(first, 1) == b'\xff\xd8x\xff\xd9'
        await gen.aclose()
        assert cam.users == 0
    asyncio.run(run())


def test_preview_releases_the_camera_when_closed(monkeypatch):
    """页面断开时关掉生成器，摄像头的使用计数要回到 0（之前漏了，ffmpeg 开了几个小时）。"""
    import asyncio
    from types import SimpleNamespace

    async def run():
        cam = camera.Camera(lambda: 'normal')
        proc = SimpleNamespace(returncode=None, terminate=lambda: None)

        async def start():
            cam.proc = proc
        monkeypatch.setattr(cam, '_start', start)
        cam.frame, cam.seq = b'\xff\xd8x\xff\xd9', 5
        gen = cam.frames()
        assert await gen.__anext__() == b'\xff\xd8x\xff\xd9' and cam.users == 1
        await gen.aclose()
        assert cam.users == 0
    asyncio.run(run())
