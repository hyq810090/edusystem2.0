"""Read-only API verification; never open the running SQLite files on the host."""
import argparse
from pathlib import Path
import httpx


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:3011')
    parser.add_argument('--data',type=Path,default=Path(__file__).resolve().parents[1]/'data')
    parser.add_argument('--repeat',type=int,default=3)
    args=parser.parse_args()
    password=(args.data/'initial-admin.txt').read_text(encoding='utf-8').splitlines()[1].split('：',1)[1]
    with httpx.Client(base_url=args.url,trust_env=False,timeout=30) as client:
        assert client.get('/healthz').is_success,'Platform unavailable'
        assert client.post('/platform/api/login',json={'username':'admin','password':password}).is_success,'Login failed; initial credentials may have changed'
        response=client.get('/platform/api/spaces');assert response.is_success
        for space in response.json():
            for _ in range(args.repeat):
                for endpoint in ('questions?page=1&page_size=1','categories','stats','papers'):
                    response=client.get(f'/bank/{space["id"]}/api/{endpoint}')
                    assert response.is_success,(space['id'],endpoint,response.status_code)
                    result=response.json()
                    if endpoint=='stats':assert result.get('status')=='success',(space['id'],'stats failed')
            total=client.get(f'/bank/{space["id"]}/api/questions?page=1&page_size=1').json()['total']
            print(f'PASS space {space["id"]} ({space["stage"]}{space["subject"]} · {space["textbook"]}): {total} questions, {args.repeat * 4} database-backed API reads')


if __name__=='__main__':main()
