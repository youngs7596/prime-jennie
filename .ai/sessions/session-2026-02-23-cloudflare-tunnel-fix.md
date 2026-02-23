# Session Handoff: Cloudflare Tunnel 재생성 — dashboard 리다이렉트 수정

## 작업 날짜
2026-02-23 (월요일 밤)

## 작업 브랜치
`development`

## 완료된 작업

### 1. Cloudflare Access 리다이렉트 문제 진단
- **증상**: `dashboard.yj-ai-lab.com` 접속 → 이메일 OTP 인증 완료 후 → 삭제된 `jenkins.yj-ai-lab.com`으로 리다이렉트 → 접속 불가
- **원인**: Jenkins용 Access Application 삭제 시 Tunnel Public Hostname과 Access Application이 별개 → Access Application의 aud tag(`2885...`)가 jenkins domain에 매핑된 채 잔존
- **진단 방법**: state 파라미터 Base64+URL 디코딩으로 hostname=dashboard인데 callback이 jenkins로 가는 것 확인

### 2. Cloudflare Tunnel 재생성
- 기존 Tunnel 삭제 + 새 Tunnel 생성 (Zero Trust Dashboard)
- Public Hostname: `dashboard.yj-ai-lab.com` → `HTTP://127.0.0.1:80` (nginx)
- jenkins 관련 설정 일체 제거

### 3. Access Application 재생성
- 기존 Access Application 전부 삭제 (jenkins aud 잔재 제거)
- 새 Self-hosted Application: `dashboard.yj-ai-lab.com` + Email OTP 정책

### 4. Tunnel 토큰 교체
- `.env`의 `CLOUDFLARE_TUNNEL_TOKEN` 새 토큰으로 교체
- `docker compose --profile infra up -d cloudflared` 재시작
- ICN 4개 커넥션 정상 등록 확인

## 현재 상태
- `dashboard.yj-ai-lab.com` 정상 접속 확인
- Cloudflare Tunnel: 새 토큰 기반, dashboard 전용
- jenkins 관련 설정 완전 제거

## 핵심 결정사항 (Key Decisions)
- Cloudflare 대시보드 디버깅보다 **Tunnel 삭제 후 재생성**이 빠르고 확실
- Access Application과 Tunnel Public Hostname은 별개 → 둘 다 정리해야 함

## 주의사항 (Warnings)
- `.env`의 `CLOUDFLARE_TUNNEL_TOKEN`은 새 값으로 교체됨 (구 토큰 무효)
- DNS CNAME에 jenkins 잔재가 있으면 삭제 필요 (이번에 확인 권고)
