#!/usr/bin/env python3
import json
from buffer_client import graphql, resolve_x_channel_id


def qstr(v):
    return json.dumps(str(v))


def main():
    cid = resolve_x_channel_id()
    c = graphql(f'''query {{ channel(input: {{id: {qstr(cid)}}}) {{ id organizationId name displayName service }} }}''')['data']['channel']
    oid = c['organizationId']
    print('channel', c)

    queries = {
        'sent_channel': f'''query {{ posts(first: 20, input: {{organizationId: {qstr(oid)}, filter: {{status:[sent], channelIds:[{qstr(cid)}]}}}}) {{ edges {{ node {{ id status text dueAt metricsUpdatedAt }} }} }} }}''',
        'all_channel': f'''query {{ posts(first: 20, input: {{organizationId: {qstr(oid)}, filter: {{channelIds:[{qstr(cid)}]}}}}) {{ edges {{ node {{ id status text dueAt metricsUpdatedAt }} }} }} }}''',
        'all_org': f'''query {{ posts(first: 20, input: {{organizationId: {qstr(oid)}}}) {{ edges {{ node {{ id status channelId text dueAt metricsUpdatedAt }} }} }} }}''',
    }
    for name, query in queries.items():
        result = graphql(query)['data']['posts']['edges']
        print(name, 'count=', len(result))
        for edge in result[:5]:
            n = edge['node']
            print(name, n.get('id'), n.get('status'), n.get('channelId'), (n.get('text') or '')[:80])


if __name__ == '__main__':
    main()
