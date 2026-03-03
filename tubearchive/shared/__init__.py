"""공용 유틸리티 함수.

프로젝트 전반에서 사용되는 작은 헬퍼 함수들을 모아둔다.
"""


def truncate_path(path: str, max_len: int = 50) -> str:
    """긴 경로를 말줄임표로 줄여 표시한다.

    경로 길이가 ``max_len`` 이하면 그대로 반환하고,
    초과하면 ``'...'`` 접두어와 함께 경로 뒷부분만 남긴다.

    Args:
        path: 원본 경로 문자열.
        max_len: 최대 출력 길이 (기본 50자).

    Returns:
        잘린 경로 문자열. 예: ``"...project/output/video.mp4"``.
    """
    if len(path) <= max_len:
        return path
    tail_len = max_len - 3
    return "..." + path[-tail_len:]
