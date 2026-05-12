# KakaoTalk Message API 검토

> 검토 기준일: 2026-05-12 KST  
> 결론: 본 프로젝트의 1차 알림 채널로는 **도입 보류**. 텍스트 또는 링크 중심의 보조 알림 채널로만 검토한다.

## 1. 검토 범위

이 문서는 카카오 디벨로퍼스의 **Kakao Talk Message API**와 “나에게 보내기” API를 중심으로 검토한다. Kakao Business의 알림톡·친구톡·브랜드 메시지 등은 별도 상품군이며, 본 문서에서는 “개인용 REST API 기반 알림 전송”의 대안으로만 간단히 언급한다.

본 프로젝트의 요구 Payload는 다음과 같다.

| Payload | 요구사항 |
|---|---|
| 영상 | 10초 내외 탐지 클립 1개 |
| 이미지 | 탐지 결과 이미지 1장 |
| 텍스트 | 상황 설명 1줄 |
| 전달 방식 | 이벤트 발생 직후 빠르게 확인 가능해야 함 |

## 2. 공식 문서 기준 핵심 사실

### 2.1 메시지 전송 목적과 수신자 제약

Kakao Talk Message API는 서비스 사용자 간 상호작용 또는 자기 자신에게 보내는 메시지에 초점이 있다. 카카오 공식 문서는 Kakao Talk Message API가 “같은 서비스를 사용하는 카카오톡 친구” 또는 “자기 자신”에게 메시지를 보낼 수 있는 기능이라고 설명한다.

또한 Kakao Talk Share와 Kakao Talk Message는 모두 사용자 간 메시징이며, 서비스가 사용자에게 직접 메시지를 보내는 목적에는 Brand Message나 Info Talk 같은 카카오 비즈니스 상품을 선택하라고 안내한다. 즉, “서버가 임의의 사용자에게 알림을 푸시한다”는 관점에서는 개인용 REST Message API가 맞지 않는다.

### 2.2 템플릿 기반 메시지 구조

Kakao Talk Message API는 기본 템플릿 또는 사용자 정의 템플릿을 사용한다. REST API의 “나에게 기본 템플릿으로 메시지 발송”도 사전 정의된 기본 템플릿 형식의 JSON을 사용자의 나와의 채팅방으로 발송하는 구조다.

기본 템플릿은 Feed, List, Commerce, Text, Scrap 등 정해진 형식을 따른다. 이 구조는 간단한 텍스트·링크·이미지 미리보기에는 적합하지만, 탐지 이벤트마다 동적으로 생성되는 영상과 이미지를 자유롭게 붙이는 알림 UI에는 제한적이다.

### 2.3 이미지 전송 제약

카카오 메시지 템플릿의 이미지 컴포넌트에는 다음 제약이 있다.

| 항목 | 공식 문서 기준 |
|---|---|
| 이미지 파일 크기 | 최대 5MB |
| 이미지 경로 | URL로 전달해야 함 |
| 로컬 이미지 경로 | 사용할 수 없음 |
| 이미지 업로드 API 경로 | 카카오 업로드 API로 업로드한 이미지 URL 사용 가능 |
| 업로드 이미지 보관 | 100일 |
| 기본 템플릿 이미지 수 | 1개 |
| 사용자 정의 템플릿 이미지 수 | 최대 3개 |

즉, 탐지 모듈이 생성한 `.jpg` 파일을 메시지 본문에 바로 첨부하는 방식은 아니다. 외부에서 접근 가능한 URL을 만들거나, 카카오 업로드 API로 먼저 올린 뒤 발급된 URL을 템플릿에 넣어야 한다.

### 2.4 영상 클립 전송 제약

Kakao Talk Message API 템플릿은 본 프로젝트가 요구하는 “MP4 파일 직접 첨부”에 적합하지 않다. 영상 자체를 첨부해 채팅방에서 바로 확인하는 구조가 아니라, 링크·스크랩·이미지 미리보기·버튼 등 템플릿 구성요소를 사용해야 한다.

따라서 10초 탐지 클립을 보내려면 다음 우회가 필요하다.

1. 클립을 별도 서버, CDN, NAS 외부 공개 URL, presigned URL 등에 업로드한다.
2. 카카오 템플릿에는 썸네일 이미지와 링크를 넣는다.
3. 수신자는 링크를 눌러 외부 페이지 또는 파일을 연다.

이 방식은 “알림 수신 즉시 미디어 확인”이라는 프로젝트 목표와 맞지 않는다.

## 3. 쿼터 및 실패 코드

Kakao Developers 쿼터 문서 기준으로 Kakao Talk Message의 일일 쿼터는 30,000건이며, 추가로 다음 세부 제한이 있다.

| 제한 | 값 |
|---|---:|
| 앱 단위 일일 요청 | 30,000 |
| sender별 일일 제한 | 100 |
| recipient별 일일 제한 | 100 |
| sender/recipient pair별 일일 제한 | 20 |

또한 오류 코드 문서에는 메시지 쿼터 초과와 관련된 오류가 명시되어 있다.

| 오류 코드 | 의미 |
|---|---|
| `-532` | sender가 특정 앱에서 일일 발송 한도를 초과 |
| `-533` | recipient가 특정 앱에서 일일 수신 한도를 초과 |
| `-536` | sender/recipient pair의 일일 한도 초과 |

탐지 이벤트가 빈번한 CCTV·IoT 환경에서는 “짧은 시간에 여러 알림이 발생”할 수 있다. 이 경우 카카오 개인용 메시지 API는 알림 누락 또는 실패가 발생할 가능성이 있다.

## 4. 구현 복잡도

KakaoTalk을 사용하려면 다음 작업이 필요하다.

| 작업 | 영향 |
|---|---|
| Kakao Login 설정 | OAuth 인증 플로우 및 토큰 갱신 필요 |
| 제품 링크 관리 | 템플릿 링크가 등록 도메인과 맞아야 함 |
| 메시지 템플릿 구성 | 기본 또는 사용자 정의 템플릿을 맞춰야 함 |
| 이미지 URL 준비 | 외부 접근 가능한 이미지 URL 또는 카카오 업로드 API 필요 |
| 영상 URL 준비 | 별도 미디어 호스팅 필요 |
| 쿼터 모니터링 | sender/recipient/pair 단위 제한 대응 필요 |
| 수신자 관리 | 같은 서비스 사용자, 친구 목록, 권한, UUID 관리 필요 |

## 5. PyKakao 라이브러리 검토

PyKakao는 Kakao Developers API를 Python에서 쉽게 사용하기 위한 오픈소스 래퍼다. PyPI 기준 최신 릴리스는 0.0.7이며 2023-12-27에 배포되었다.

본 프로젝트에서는 PyKakao를 아키텍처 의존성으로 두지 않는 편이 낫다. 이유는 다음과 같다.

1. 카카오 공식 REST API가 직접 호출하기 어렵지 않다.
2. 본 프로젝트의 병목은 Python 래퍼가 아니라 KakaoTalk Message API의 미디어·수신자·템플릿 제약이다.
3. 장기 운영 시에는 래퍼보다 공식 REST API와 공식 문서를 기준으로 유지보수하는 편이 안전하다.

## 6. 도입 가능 시나리오

KakaoTalk을 완전히 배제할 필요는 없다. 다음 조건에서는 보조 채널로 사용할 수 있다.

| 시나리오 | 적합성 |
|---|---|
| 텍스트 1줄만 나에게 보내기 | 가능 |
| 이미지 1장과 상세 페이지 링크 | 가능하나 URL/템플릿 필요 |
| “심각 이벤트 발생”만 요약 전달 | 가능 |
| 영상 클립을 직접 확인해야 하는 알림 | 부적합 |
| 외부 고객 대상 공식 알림 | Kakao Business 알림톡/친구톡 별도 검토 필요 |

## 7. 본 프로젝트 기준 판단

본 프로젝트에서는 KakaoTalk Message API를 1차 도입하지 않는다.

주요 사유는 다음과 같다.

1. **영상 클립 직접 첨부가 어렵다.** 10초 MP4를 메시지에 바로 붙이는 구조가 아니다.
2. **이미지도 URL 기반이다.** 로컬 파일을 바로 보내는 구조가 아니므로 호스팅 또는 업로드 API가 필요하다.
3. **템플릿 제약이 크다.** 탐지 이벤트별 동적 미디어를 자유롭게 보여주기 어렵다.
4. **수신자·권한·쿼터 제약이 강하다.** 운영 알림이 빈번한 환경에 불리하다.
5. **개인용 REST Message API의 목적과 다르다.** 서버가 사용자에게 직접 푸시하는 알림 채널로 쓰기에는 부적합하다.

## 8. 검증 체크리스트

카카오톡을 보조 채널로 재검토할 경우 다음을 먼저 확인한다.

- [ ] 실제 수신자가 “나에게 보내기”인지, 같은 서비스 사용자 간 메시지인지, 외부 고객 알림인지 구분
- [ ] 영상은 링크로 충분한지, 채팅방 내 직접 첨부가 필요한지 결정
- [ ] 이미지 URL이 외부망에서 접근 가능한지 확인
- [ ] 이미지 파일 크기가 5MB 이하인지 확인
- [ ] Product Link의 등록 도메인과 메시지 링크 도메인이 일치하는지 확인
- [ ] sender/recipient/pair 쿼터를 실제 이벤트 빈도로 계산
- [ ] 장기 운영 시 Kakao Business 상품이 필요한지 검토

## 9. 참고 자료

- Kakao Developers — Kakao Talk Message Concepts: https://developers.kakao.com/docs/en/kakaotalk-message/common
- Kakao Developers — Kakao Talk Message REST API: https://developers.kakao.com/docs/ko/kakaotalk-message/rest-api
- Kakao Developers — Message Template Common: https://developers.kakao.com/docs/en/message-template/common
- Kakao Developers — Message Template FAQ: https://developers.kakao.com/docs/en/message-template/faq
- Kakao Developers — Quota: https://developers.kakao.com/docs/en/getting-started/quota
- Kakao Developers — Error Code: https://developers.kakao.com/docs/en/rest-api/error-code
- PyPI — PyKakao: https://pypi.org/project/PyKakao/
- GitHub — PyKakao: https://github.com/WooilJeong/PyKakao
