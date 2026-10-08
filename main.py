import os, sqlite3, uuid
from contextlib import closing
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(title='Student Hub Online API', version='1.0.0')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=False, allow_methods=['*'], allow_headers=['*'])
DB = os.getenv('DATABASE_URL', '').strip()
SQLITE = os.getenv('SQLITE_PATH', '/tmp/student_hub.db')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS players (client_id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, xp INTEGER NOT NULL DEFAULT 0, level INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS friend_requests (from_id TEXT NOT NULL, to_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(from_id,to_id));
CREATE TABLE IF NOT EXISTS friends (user_id TEXT NOT NULL, friend_id TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(user_id,friend_id));
CREATE TABLE IF NOT EXISTS challenges (id TEXT PRIMARY KEY, from_id TEXT NOT NULL, to_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', from_score INTEGER, to_score INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
'''

def conn():
    # PostgreSQL support can be enabled by setting DATABASE_URL and installing psycopg.
    if DB:
        import psycopg
        c = psycopg.connect(DB)
        c.autocommit = True
        return c
    c = sqlite3.connect(SQLITE, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c

def init_db():
    c = conn()
    try:
        if DB:
            with c.cursor() as cur:
                cur.execute(SCHEMA.replace('INTEGER PRIMARY KEY','INTEGER PRIMARY KEY'))
        else:
            c.executescript(SCHEMA)
    finally: c.close()
init_db()

class Player(BaseModel):
    client_id: str = Field(min_length=8, max_length=100)
    username: str = Field(min_length=1, max_length=24)
    xp: int = Field(ge=0, le=10_000_000)
    level: int = Field(ge=1, le=100_000)
class FriendRequest(BaseModel):
    from_id: str
    to_id: str
class FriendResponse(BaseModel):
    user_id: str
    from_id: str
    decision: str
class ChallengeCreate(BaseModel):
    from_id: str
    to_id: str
class ChallengeResponse(BaseModel):
    challenge_id: str
    user_id: str
    decision: str
class ChallengeScore(BaseModel):
    challenge_id: str
    user_id: str
    score: int = Field(ge=0, le=100)

def q(sql, params=(), fetch=False):
    c=conn()
    try:
        if DB:
            with c.cursor() as cur:
                cur.execute(sql, params)
                if fetch:
                    cols=[d.name for d in cur.description]
                    return [dict(zip(cols,r)) for r in cur.fetchall()]
                return []
        cur=c.execute(sql, params)
        if fetch: return [dict(r) for r in cur.fetchall()]
        c.commit(); return []
    finally: c.close()

def one(sql, params=()):
    rows=q(sql,params,True); return rows[0] if rows else None

def player(pid):
    return one('SELECT client_id,username,xp,level FROM players WHERE client_id=?' if not DB else 'SELECT client_id,username,xp,level FROM players WHERE client_id=%s',(pid,))

def ensure_player(p: Player):
    existing=player(p.client_id)
    if existing:
        # Username belongs to another account -> keep the existing username stable.
        p.username=existing['username']
    try:
        if DB:
            q('''INSERT INTO players(client_id,username,xp,level) VALUES(%s,%s,%s,%s)
                 ON CONFLICT(client_id) DO UPDATE SET xp=EXCLUDED.xp,level=EXCLUDED.level,updated_at=CURRENT_TIMESTAMP''',(p.client_id,p.username,p.xp,p.level))
        else:
            q('INSERT INTO players(client_id,username,xp,level) VALUES(?,?,?,?) ON CONFLICT(client_id) DO UPDATE SET xp=excluded.xp,level=excluded.level,updated_at=CURRENT_TIMESTAMP',(p.client_id,p.username,p.xp,p.level))
    except Exception as e:
        # If username is taken, surface a clean error.
        raise HTTPException(409,'That username is already used by another player.') from e
    return player(p.client_id)

@app.get('/')
def root(): return {'ok':True,'service':'Student Hub Online API'}
@app.get('/health')
def health(): return {'ok':True}
@app.post('/players/sync')
def sync(p: Player): return {'player':ensure_player(p)}
@app.get('/players/search')
def search(qry: str = Query('', alias='q', max_length=24)):
    if DB:
        rows=q('SELECT client_id,username,xp,level FROM players WHERE LOWER(username) LIKE LOWER(%s) ORDER BY xp DESC LIMIT 20',(f'%{qry}%',),True)
    else:
        rows=q('SELECT client_id,username,xp,level FROM players WHERE LOWER(username) LIKE LOWER(?) ORDER BY xp DESC LIMIT 20',(f'%{qry}%',),True)
    return {'players':rows}
@app.get('/leaderboard')
def leaderboard(limit:int=Query(50,ge=1,le=100)):
    rows=q(f'SELECT client_id,username,xp,level FROM players ORDER BY xp DESC, username ASC LIMIT {limit}',fetch=True)
    return {'players':rows}

@app.post('/friends/request')
def request_friend(r: FriendRequest):
    if r.from_id==r.to_id: raise HTTPException(400,'You cannot add yourself.')
    if not player(r.from_id) or not player(r.to_id): raise HTTPException(404,'Player not found.')
    # Already friends?
    if DB: existing=one('SELECT 1 FROM friends WHERE user_id=%s AND friend_id=%s',(r.from_id,r.to_id))
    else: existing=one('SELECT 1 FROM friends WHERE user_id=? AND friend_id=?',(r.from_id,r.to_id))
    if existing: raise HTTPException(409,'You are already friends.')
    try:
        if DB: q('INSERT INTO friend_requests(from_id,to_id,status) VALUES(%s,%s,\'pending\') ON CONFLICT(from_id,to_id) DO UPDATE SET status=\'pending\'',(r.from_id,r.to_id))
        else: q('INSERT OR REPLACE INTO friend_requests(from_id,to_id,status) VALUES(?, ?, \'pending\')',(r.from_id,r.to_id))
    except Exception as e: raise HTTPException(409,'A request already exists.') from e
    return {'ok':True}

@app.get('/friends')
def friends(client_id:str):
    if DB:
        req=q('SELECT p.client_id,p.username,p.xp,p.level FROM friend_requests r JOIN players p ON p.client_id=r.from_id WHERE r.to_id=%s AND r.status=\'pending\'',(client_id,),True)
        fs=q('''SELECT p.client_id,p.username,p.xp,p.level FROM friends f JOIN players p ON p.client_id=f.friend_id WHERE f.user_id=%s ORDER BY p.xp DESC''',(client_id,),True)
    else:
        req=q("SELECT p.client_id,p.username,p.xp,p.level FROM friend_requests r JOIN players p ON p.client_id=r.from_id WHERE r.to_id=? AND r.status='pending'",(client_id,),True)
        fs=q('SELECT p.client_id,p.username,p.xp,p.level FROM friends f JOIN players p ON p.client_id=f.friend_id WHERE f.user_id=? ORDER BY p.xp DESC',(client_id,),True)
    return {'requests':req,'friends':fs}

@app.post('/friends/respond')
def respond_friend(r: FriendResponse):
    if r.decision not in ('accepted','declined'): raise HTTPException(400,'Invalid decision.')
    if DB: req=one('SELECT from_id,to_id FROM friend_requests WHERE from_id=%s AND to_id=%s AND status=\'pending\'',(r.from_id,r.user_id))
    else: req=one("SELECT from_id,to_id FROM friend_requests WHERE from_id=? AND to_id=? AND status='pending'",(r.from_id,r.user_id))
    if not req: raise HTTPException(404,'Friend request not found.')
    if r.decision=='declined':
        q('DELETE FROM friend_requests WHERE from_id=%s AND to_id=%s' if DB else 'DELETE FROM friend_requests WHERE from_id=? AND to_id=?',(r.from_id,r.user_id)); return {'ok':True}
    if DB:
        q('INSERT INTO friends(user_id,friend_id) VALUES(%s,%s) ON CONFLICT DO NOTHING',(r.user_id,r.from_id)); q('INSERT INTO friends(user_id,friend_id) VALUES(%s,%s) ON CONFLICT DO NOTHING',(r.from_id,r.user_id)); q('DELETE FROM friend_requests WHERE from_id=%s AND to_id=%s',(r.from_id,r.user_id))
    else:
        q('INSERT OR IGNORE INTO friends(user_id,friend_id) VALUES(?,?)',(r.user_id,r.from_id)); q('INSERT OR IGNORE INTO friends(user_id,friend_id) VALUES(?,?)',(r.from_id,r.user_id)); q('DELETE FROM friend_requests WHERE from_id=? AND to_id=?',(r.from_id,r.user_id))
    return {'ok':True}

@app.post('/challenges')
def create_challenge(r: ChallengeCreate):
    if r.from_id==r.to_id: raise HTTPException(400,'You cannot challenge yourself.')
    # Require friendship.
    if DB: ok=one('SELECT 1 FROM friends WHERE user_id=%s AND friend_id=%s',(r.from_id,r.to_id))
    else: ok=one('SELECT 1 FROM friends WHERE user_id=? AND friend_id=?',(r.from_id,r.to_id))
    if not ok: raise HTTPException(403,'Add this player as a friend first.')
    cid=uuid.uuid4().hex[:12]
    if DB: q('INSERT INTO challenges(id,from_id,to_id) VALUES(%s,%s,%s)',(cid,r.from_id,r.to_id))
    else: q('INSERT INTO challenges(id,from_id,to_id) VALUES(?,?,?)',(cid,r.from_id,r.to_id))
    return {'id':cid}

@app.get('/challenges')
def get_challenges(client_id:str):
    sql='''SELECT c.id,c.from_id,c.to_id,c.status,c.from_score,c.to_score,
           pf.username AS from_name, pt.username AS to_name
           FROM challenges c JOIN players pf ON pf.client_id=c.from_id JOIN players pt ON pt.client_id=c.to_id
           WHERE c.from_id=%s OR c.to_id=%s ORDER BY c.created_at DESC LIMIT 30'''
    if not DB: sql=sql.replace('%s','?')
    rows=q(sql,(client_id,client_id),True)
    out=[]
    for c in rows:
        mine=c['from_id']==client_id
        out.append({'id':c['id'],'status':c['status'],'opponent_name':c['to_name'] if mine else c['from_name'],'my_score':c['from_score'] if mine else c['to_score'],'opponent_score':c['to_score'] if mine else c['from_score'],'is_recipient':not mine})
    return {'challenges':out}

@app.post('/challenges/respond')
def challenge_respond(r: ChallengeResponse):
    if DB: c=one('SELECT id,from_id,to_id,status FROM challenges WHERE id=%s',(r.challenge_id,))
    else: c=one('SELECT id,from_id,to_id,status FROM challenges WHERE id=?',(r.challenge_id,))
    if not c or c['to_id']!=r.user_id: raise HTTPException(404,'Challenge not found.')
    if r.decision not in ('accept','decline'): raise HTTPException(400,'Invalid decision.')
    status='accepted' if r.decision=='accept' else 'declined'
    q('UPDATE challenges SET status=%s WHERE id=%s' if DB else 'UPDATE challenges SET status=? WHERE id=?',(status,r.challenge_id))
    return {'ok':True}

@app.post('/challenges/score')
def challenge_score(r: ChallengeScore):
    if DB: c=one('SELECT * FROM challenges WHERE id=%s',(r.challenge_id,))
    else: c=one('SELECT * FROM challenges WHERE id=?',(r.challenge_id,))
    if not c or r.user_id not in (c['from_id'],c['to_id']): raise HTTPException(404,'Challenge not found.')
    if c['status']!='accepted': raise HTTPException(400,'Challenge is not accepted yet.')
    if c['from_id']==r.user_id:
        q('UPDATE challenges SET from_score=%s WHERE id=%s' if DB else 'UPDATE challenges SET from_score=? WHERE id=?',(r.score,r.challenge_id))
    else:
        q('UPDATE challenges SET to_score=%s WHERE id=%s' if DB else 'UPDATE challenges SET to_score=? WHERE id=?',(r.score,r.challenge_id))
    c=one('SELECT * FROM challenges WHERE id=%s' if DB else 'SELECT * FROM challenges WHERE id=?',(r.challenge_id,))
    if c['from_score'] is not None and c['to_score'] is not None:
        q('UPDATE challenges SET status=%s WHERE id=%s' if DB else 'UPDATE challenges SET status=? WHERE id=?',('finished',r.challenge_id))
    return {'ok':True}

class ChatMessage(BaseModel):
    from_id: str
    to_id: str
    message: str = Field(min_length=1, max_length=1000)

@app.post('/chat/send')
def chat_send(m: ChatMessage):
    if m.from_id == m.to_id:
        raise HTTPException(400, 'You cannot message yourself.')
    if not player(m.from_id) or not player(m.to_id):
        raise HTTPException(404, 'Player not found.')
    create_sql = "CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, from_id TEXT NOT NULL, to_id TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    q(create_sql)
    mid = uuid.uuid4().hex
    if DB:
        q('INSERT INTO messages(id,from_id,to_id,message) VALUES(%s,%s,%s,%s)', (mid,m.from_id,m.to_id,m.message.strip()))
    else:
        q('INSERT INTO messages(id,from_id,to_id,message) VALUES(?,?,?,?)', (mid,m.from_id,m.to_id,m.message.strip()))
    return {'ok': True, 'id': mid}

@app.get('/chat')
def chat_history(user_id: str, other_id: str):
    sql = '''SELECT id,from_id,to_id,message,created_at FROM messages
             WHERE (from_id=%s AND to_id=%s) OR (from_id=%s AND to_id=%s)
             ORDER BY created_at ASC LIMIT 200'''
    if not DB:
        sql = sql.replace('%s', '?')
    return {'messages': q(sql, (user_id,other_id,other_id,user_id), True)}
