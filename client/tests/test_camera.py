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
    assert graph('90').startswith('[0:v]split=2')                   # 横屏放在支架上：本来就是正的
    assert graph('normal').startswith('[0:v]transpose=1,split=2')   # 竖屏：顺时针转 90°
    assert graph('180').startswith('[0:v]transpose=2,')
    assert graph('270').startswith('[0:v]hflip,vflip,')
    assert 'hflip[pv]' in graph('normal') and '[st]' in graph('normal')   # 预览镜像，整图不镜像
    assert 'scale=-2:640' in graph('normal') and 'scale=-2:360' in graph('90')
