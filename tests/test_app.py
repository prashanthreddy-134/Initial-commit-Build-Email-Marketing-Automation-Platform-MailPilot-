import io, pytest
from app import create_app, db, User, Subscriber, Campaign, CampaignRecipient, Segment, SegmentMember, EmailTemplate, AutomationSequence, SequenceEnrollment

@pytest.fixture()
def app():
    app=create_app({"TESTING":True,"SECRET_KEY":"test-secret","SQLALCHEMY_DATABASE_URI":"sqlite://","MAIL_MODE":"console"})
    with app.app_context(): db.create_all()
    yield app
    with app.app_context(): db.session.remove(); db.drop_all()

@pytest.fixture()
def client(app): return app.test_client()

def register(client):
    return client.post("/register",data={"username":"tester","email":"tester@example.com","password":"strongpass123"},follow_redirects=True)

def test_health(client): assert client.get("/health").json=={"status":"ok"}
def test_register_login_logout(client):
    r=register(client); assert b"Account created" in r.data
    assert client.get("/dashboard").status_code==200
    client.post("/logout"); assert client.get("/dashboard").status_code==302
    assert client.post("/login",data={"identity":"tester","password":"strongpass123"}).status_code==302
def test_duplicate_registration(client):
    register(client); client.post("/logout")
    r=client.post("/register",data={"username":"tester","email":"tester@example.com","password":"strongpass123"},follow_redirects=True)
    assert b"already exists" in r.data
def test_password_minimum(client):
    r=client.post("/register",data={"username":"ab","email":"bad","password":"123"},follow_redirects=True)
    assert b"Enter a username" in r.data
def test_subscriber_requires_consent(client):
    register(client); r=client.post("/subscribers",data={"email":"a@example.com"},follow_redirects=True)
    assert b"Explicit consent" in r.data
def test_add_and_toggle_subscriber(client,app):
    register(client); client.post("/subscribers",data={"email":"a@example.com","consent":"yes"})
    with app.app_context(): s=Subscriber.query.filter_by(email="a@example.com").one(); sid=s.id; assert s.is_active
    client.post(f"/subscribers/{sid}/toggle")
    with app.app_context(): assert not db.session.get(Subscriber,sid).is_active
def test_csv_import_only_consented(client,app):
    register(client); data={"file":(io.BytesIO(b"email,consent,first_name\na@example.com,yes,A\nb@example.com,no,B\n"),"list.csv")}
    r=client.post("/subscribers/import",data=data,content_type="multipart/form-data",follow_redirects=True)
    assert b"Imported 1" in r.data
    with app.app_context(): assert Subscriber.query.count()==1
def test_template_create(client,app):
    register(client); client.post("/templates",data={"name":"Welcome","subject":"Hi","html_content":"<p>Hello</p>"})
    with app.app_context(): assert EmailTemplate.query.count()==1
def test_campaign_create_and_send(client,app):
    register(client); client.post("/subscribers",data={"email":"a@example.com","consent":"yes"})
    client.post("/campaigns",data={"name":"Launch","subject":"Hello","html_content":"<p>Hi</p>"})
    with app.app_context(): cid=Campaign.query.one().id
    r=client.post(f"/campaigns/{cid}/send",follow_redirects=True); assert b"processed for 1" in r.data
    with app.app_context(): assert Campaign.query.get(cid).status=="sent"; assert CampaignRecipient.query.count()==1
def test_campaign_cannot_send_twice(client,app):
    register(client); client.post("/subscribers",data={"email":"a@example.com","consent":"yes"})
    client.post("/campaigns",data={"name":"Launch","subject":"Hello","html_content":"Hi"})
    with app.app_context(): cid=Campaign.query.one().id
    client.post(f"/campaigns/{cid}/send"); r=client.post(f"/campaigns/{cid}/send",follow_redirects=True)
    assert b"Only draft or paused" in r.data
def test_segment_create(client,app):
    register(client); client.post("/subscribers",data={"email":"a@example.com","consent":"yes"})
    with app.app_context(): sid=Subscriber.query.one().id
    client.post("/segments",data={"name":"VIP","subscriber_ids":str(sid)})
    with app.app_context(): assert Segment.query.count()==1 and SegmentMember.query.count()==1
def test_tracking_pixel(client,app):
    register(client); client.post("/subscribers",data={"email":"a@example.com","consent":"yes"})
    client.post("/campaigns",data={"name":"C","subject":"S","html_content":"Hi"})
    with app.app_context(): c=Campaign.query.one(); s=Subscriber.query.one(); db.session.add(CampaignRecipient(campaign_id=c.id,subscriber_id=s.id)); db.session.commit(); cid,sid=c.id,s.id
    r=client.get(f"/track/open/{cid}/{sid}.gif"); assert r.status_code==200 and r.mimetype=="image/gif"
    with app.app_context(): assert CampaignRecipient.query.one().opened_at is not None
def test_click_tracking_rejects_bad_url(client): assert client.get("/track/click/1/1?target=javascript:alert(1)").status_code==400
def test_click_tracking_records_and_redirects(client,app):
    register(client); client.post("/subscribers",data={"email":"a@example.com","consent":"yes"})
    client.post("/campaigns",data={"name":"C","subject":"S","html_content":"Hi"})
    with app.app_context(): c=Campaign.query.one(); s=Subscriber.query.one(); db.session.add(CampaignRecipient(campaign_id=c.id,subscriber_id=s.id)); db.session.commit(); cid,sid=c.id,s.id
    r=client.get(f"/track/click/{cid}/{sid}?target=https://example.org",follow_redirects=False)
    assert r.status_code==302 and r.location=="https://example.org"
    with app.app_context(): assert CampaignRecipient.query.one().clicked_at is not None
def test_sequence_enrollment(client,app):
    register(client); client.post("/subscribers",data={"email":"a@example.com","consent":"yes"})
    client.post("/sequences",data={"name":"Welcome","subject":"Hi","html_content":"Hello","delay_days":"2"})
    with app.app_context(): seq=AutomationSequence.query.one(); sub=Subscriber.query.one(); seqid,sid=seq.id,sub.id
    client.post(f"/sequences/{seqid}/enroll",data={"subscriber_ids":str(sid)})
    with app.app_context(): assert SequenceEnrollment.query.count()==1
def test_analytics_page(client):
    register(client); assert client.get("/analytics").status_code==200
def test_campaign_isolation(client,app):
    register(client); client.post("/campaigns",data={"name":"Mine","subject":"S","html_content":"Hi"})
    with app.app_context(): cid=Campaign.query.one().id
    client.post("/logout"); client.post("/register",data={"username":"other","email":"other@example.com","password":"strongpass123"})
    assert client.post(f"/campaigns/{cid}/send").status_code==404


def test_unsubscribe_token_deactivates_subscriber(client,app):
    register(client); client.post("/subscribers",data={"email":"a@example.com","consent":"yes"})
    with app.app_context():
        from itsdangerous import URLSafeTimedSerializer
        sid=Subscriber.query.one().id
        token=URLSafeTimedSerializer(app.config["SECRET_KEY"]).dumps(sid,salt="unsubscribe")
    r=client.get(f"/unsubscribe/{token}")
    assert r.status_code==200
    with app.app_context(): assert not Subscriber.query.one().is_active
