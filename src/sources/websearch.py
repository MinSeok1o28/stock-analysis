"""3차 출처: 웹검색. 정성 전용.

수치를 반환하는 함수를 이 파일에 두지 않는다. Sourced가 3차 수치를 거부하므로
만들어도 예외가 나지만, 애초에 경로를 만들지 않는 게 구조적 방어다.

실제 검색은 Claude가 WebSearch 도구로 수행하고, 그 결과를 note()로 감싸 시스템에 넣는다.
"""

from __future__ import annotations

from ..provenance import Sourced, web


def note(text: str, url: str, source_name: str = "웹검색") -> Sourced[str]:
    """뉴스·테마·시나리오 등 정성 관찰 1건. 수치는 담지 않는다."""
    return Sourced(text, web(source_name, url))
