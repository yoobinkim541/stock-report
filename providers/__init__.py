# -*- coding: utf-8 -*-
"""providers — 데이터/상태 접근층 (외부 피드·캐시·상태파일).

barbell_strategy.py 의 god-module 에서 데이터 수집층을 분리한 패키지.
전략/리포트 로직(barbell_strategy)은 이 패키지를 import 하지만,
이 패키지는 barbell_strategy 의 어떤 것에도 의존하지 않는다(순환참조 금지).
"""
