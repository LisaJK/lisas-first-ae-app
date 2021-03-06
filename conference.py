from datetime import datetime

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import StringMessage
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForms
from models import TeeShirtSize
from models import Session
from models import SessionForm
from models import SessionForms
from models import Speaker
from models import SpeakerForm

from settings import WEB_CLIENT_ID
from settings import ANDROID_CLIENT_ID
from settings import IOS_CLIENT_ID
from settings import ANDROID_AUDIENCE

from utils import getUserId

# !/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21
extended by Lisa Kugler

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = 'RECENT_ANNOUNCEMENTS'
ANNOUNCEMENT_TPL = ('Last chance to attend! The following conferences '
                    'are nearly sold out: %s')
MEMCACHE_FEATURE_KEY = 'FEATURED_SPEAKER'
FEATURED_SPEAKER_TPL = ('The featured speaker is: %s (Sessions: %s)')
# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS_CONF = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": ["Default", "Topic"],
}

DEFAULTS_SESSION = {
    "speaker": "N.N.",
    "duration": 0,
    "highlights": ["Default", "Highlight"],
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS = {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
        }

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1)
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1)
)

SESSION_GET_REQUEST = endpoints.ResourceContainer(
    websafeSessionKey=messages.StringField(1, required=True)
)

SESSION_TYPE_GET_REQUEST = endpoints.ResourceContainer(
    websafeConferenceKey=messages.StringField(1, required=True),
    typeOfSession=messages.StringField(2)
)

SESSION_TIME_EXCLTYPES_GET_REQUEST = endpoints.ResourceContainer(
    websafeConferenceKey=messages.StringField(1, required=True),
    startTime=messages.StringField(2),
    excludedTypes=messages.StringField(3, repeated=True)
)

SESSION_POST_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeSessionKey=messages.StringField(1)
)

SPEAKER_GET_REQUEST = endpoints.ResourceContainer(
    speakerName=messages.StringField(1, required=True),
)

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api(name='conference', version='v1', audiences=[ANDROID_AUDIENCE],
               allowed_client_ids=[WEB_CLIENT_ID,
                                   API_EXPLORER_CLIENT_ID,
                                   ANDROID_CLIENT_ID,
                                   IOS_CLIENT_ID],
               scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeConferenceKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf

    def _createConferenceObject(self, request):
        """Create or update Conference object,
           returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException(
                  "Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name)
                for field in request.all_fields()}
        del data['websafeConferenceKey']
        del data['organizerDisplayName']

        # add default values for those missing
        # (both data model & outbound Message)
        for df in DEFAULTS_CONF:
            if data[df] in (None, []):
                data[df] = DEFAULTS_CONF[df]
                setattr(request, df, DEFAULTS_CONF[df])

        # convert dates from strings to Date objects;
        # set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10],
                                                  "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10],
                                                "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
                              'conferenceInfo': repr(request)},
                      url='/tasks/send_confirmation_email'
                      )
        return request

    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name)
                for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                    'No conference found with key: %s' %
                    request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))

    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
                      http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)

    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
                      path='conference/{websafeConferenceKey}',
                      http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)

    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
                      path='conference/{websafeConferenceKey}',
                      http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = self._getConf(request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='getConferencesCreated',
                      http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf,
                                              getattr(prof, 'displayName'))
                   for conf in confs]
        )

    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"],
                                                   filtr["operator"],
                                                   filtr["value"])
            q = q.filter(formatted_query)
        return q

    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name)
                     for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException(
                    "Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in
                # previous filters
                # disallow the filter if inequality was performed
                # on a different field before
                # track the field on which the inequality operation
                # is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException(
                        "Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)

    @endpoints.method(ConferenceQueryForms, ConferenceForms,
                      path='queryConferences',
                      http_method='POST',
                      name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId))
                      for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
                items=[self._copyConferenceToForm(conf,
                                                  names[conf.organizerUserId])
                       for conf in conferences]
        )

    def _getConf(self, websafeConferenceKey):
        """Returns Conference object; bail if not found"""
        conf_key = ndb.Key(urlsafe=websafeConferenceKey)
        conf = conf_key.get()
        if not conf:
            raise endpoints.NotFoundException(
                    'No conference found with key: %s' %
                    websafeConferenceKey)
        return conf


# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name,
                            getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf

    def _getProfileFromUser(self):
        """Return user Profile from datastore,
           creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key=p_key,
                displayName=user.nickname(),
                mainEmail=user.email(),
                teeShirtSize=str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile

    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        # if field == 'teeShirtSize':
                        #    setattr(prof, field, str(val).upper())
                        # else:
                        #    setattr(prof, field, val)
                        prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)

    @endpoints.method(message_types.VoidMessage, ProfileForm,
                      path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()

    @endpoints.method(ProfileMiniForm, ProfileForm,
                      path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""

        return self._doProfile(request)


# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = ANNOUNCEMENT_TPL % (
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement

    @endpoints.method(message_types.VoidMessage, StringMessage,
                      path='conference/announcement/get',
                      http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        return StringMessage(
            data=memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY) or "")


# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        # get user Profile
        prof = self._getProfileFromUser()

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='conferences/attending',
                      http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        # get user Profile
        prof = self._getProfileFromUser()
        conf_keys = [ndb.Key(urlsafe=wsck)
                     for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId)
                      for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf,
                                              names[conf.organizerUserId])
                   for conf in conferences]
        )

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
                      path='conference/{websafeConferenceKey}',
                      http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
                      path='conference/{websafeConferenceKey}',
                      http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='filterPlayground',
                      http_method='GET', name='filterPlayground')
    def filterPlayground(self, request):
        """Filter Playground"""
        q = Conference.query()
        # field = "city"
        # operator = "="
        # value = "London"
        # f = ndb.query.FilterNode(field, operator, value)
        # q = q.filter(f)
        q = q.filter(Conference.city == "London")
        q = q.filter(Conference.topics == "Medical Innovations")
        q = q.filter(Conference.month == 6)

        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") for conf in q]
        )

    # - - - Session objects - - - - - - - - - - - - - - - - -

    @endpoints.method(SessionForm,
                      SessionForm,
                      http_method='POST',
                      name='createSession')
    def createSession(self, request):
        """Create new session."""
        return self._createSessionObject(request)

    def _createSessionObject(self, request):
        """Create Session object,
           returning SessionForm."""
        # get the user
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # make sure the request contains the session name
        if not request.name:
            raise endpoints.BadRequestException(
                  "Session 'name' field required")

        # get the conference the session is in
        conf = self._getConf(request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can add sessions to a conference.')

        # copy SessionForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name)
                for field in request.all_fields()}

        # add default values for those missing
        # (both data model & outbound Message)
        for df in DEFAULTS_SESSION:
            if data[df] in (None, []):
                data[df] = DEFAULTS_SESSION[df]
                setattr(request, df, DEFAULTS_SESSION[df])

        # convert dates from strings to Date objects;
        if data['date']:
            data['date'] = datetime.strptime(data['date'][:10],
                                             "%Y-%m-%d").date()

        # convert start time from string to Time objects;
        if data['startTime']:
            data['startTime'] = datetime.strptime(
                data['startTime'], "%H:%M").time()

        # check that the speaker exists
        if data['speaker']:
            speaker = Speaker.query(Speaker.name == request.speaker).get()

            if not speaker:
                raise endpoints.NotFoundException(
                    'No speaker found with name: %s' %
                    request.speaker)

        # generate session key
        s_id = Session.allocate_ids(size=1, parent=conf.key)[0]
        s_key = ndb.Key(Session, s_id, parent=conf.key)
        data['key'] = s_key

        del data['websafeConferenceKey']
        del data['websafeSessionKey']

        # create session in data store and return request
        Session(**data).put()

        # add websafeSessionKey to the request
        request.websafeSessionKey = s_key.urlsafe()

        # check the speaker and add task to the queue
        sessions_of_speaker = Session.query(Session.speaker == data['speaker'],
                                            ancestor=conf.key).fetch()
        speaker_sessions = ', '.join(
            session.name for session in sessions_of_speaker)
        if len(sessions_of_speaker) > 1:
            taskqueue.add(url='/tasks/set_featured_speaker',
                          params={'sessions': speaker_sessions,
                                  'speaker': data['speaker']},
                          method='GET')

        return request

    @endpoints.method(SESSION_GET_REQUEST,
                      SessionForm,
                      http_method='GET',
                      name='getSession')
    def getSession(self, request):
        """Return requested session (by websafeSessionKey)."""
        # get Session object from request; bail if not found
        session = ndb.Key(urlsafe=request.websafeSessionKey).get()
        if not session:
            raise endpoints.NotFoundException(
                    'No session found with key: %s' %
                    request.websafeSessionKey)
        # return SessionForm
        return self._copySessionToForm(session)

    @endpoints.method(CONF_POST_REQUEST, SessionForms,
                      path='getConferenceSessions',
                      http_method='POST',
                      name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Return all sessions of the given conference"""

        # get the conference
        conf = self._getConf(request.websafeConferenceKey)

        # get all sessions of the conference
        sessions = Session.query(ancestor=conf.key)
        return SessionForms(items=[self._copySessionToForm(session)
                            for session in sessions])

    @endpoints.method(SESSION_POST_REQUEST,
                      SessionForm,
                      http_method='PUT',
                      name='updateSession')
    def updateSession(self, request):
        """Update session w/provided fields & return w/updated info."""
        return self._updateSessionObject(request)

    @ndb.transactional()
    def _updateSessionObject(self, request):
        # get user id
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy SessionForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name)
                for field in request.all_fields()}

        # get session by websafeSessionKey
        session = ndb.Key(urlsafe=request.websafeSessionKey).get()

        # check that session exists
        if not session:
            raise endpoints.NotFoundException(
                    'No session found with key: %s' %
                    request.websafeSessionKey)

        # check that user is owner of the conference
        conf = session.key.parent().get()

        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the session.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from SessionForm to Session object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name == 'date':
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                if field.name == 'startTime':
                    data = datetime.strptime(data, "%H:%M").time()
                # write to Session object
                setattr(session, field.name, data)
        # update session in data store
        session.put()
        return self._copySessionToForm(session)

    def _copySessionToForm(self, session):
        """Copy relevant fields from Session to SessionForm."""
        sf = SessionForm()
        for field in sf.all_fields():
            if hasattr(session, field.name):
                # convert Date to date string; just copy others
                if field.name == "date" or field.name == "startTime":
                    setattr(sf, field.name, str(getattr(session, field.name)))
                else:
                    setattr(sf, field.name, getattr(session, field.name))
            elif field.name == "websafeSessionKey":
                    setattr(sf, field.name, session.key.urlsafe())
            elif field.name == "websafeConferenceKey":
                    setattr(sf, field.name, session.key.parent().urlsafe())
        sf.check_initialized()
        return sf

    @endpoints.method(SESSION_TYPE_GET_REQUEST,
                      SessionForms,
                      http_method='GET',
                      name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """ Get all sessions of the conference of the given type"""
        # get the conference
        conf = self._getConf(request.websafeConferenceKey)

        # get all sessions of the conference with the given type
        sessions = Session.query(
                              Session.type == request.typeOfSession,
                              ancestor=conf.key).fetch()

        # return set of SessionForm objects per Session
        return SessionForms(
            items=[self._copySessionToForm(session)
                   for session in sessions]
        )

    @endpoints.method(SPEAKER_GET_REQUEST,
                      SessionForms,
                      http_method='GET',
                      name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Get all sessions given by the speaker across all conferences"""
        # get the speaker
        speaker = Speaker.query(Speaker.name == request.speakerName).get()

        if not speaker:
                raise endpoints.NotFoundException(
                    'No speaker found with key: %s' %
                    request.speakerName)

        # get the sessions of this speaker
        sessions = Session.query(Session.speaker == speaker.name).fetch()

        # return set of SessionForm objects per Session
        return SessionForms(
            items=[self._copySessionToForm(session)
                   for session in sessions]
        )

    @endpoints.method(SpeakerForm,
                      SpeakerForm,
                      http_method='POST',
                      name='createSpeaker')
    def createSpeaker(self, request):
        """Create new speaker."""
        return self._createSpeakerObject(request)

    def _createSpeakerObject(self, request):
        """Create Speaker object,
           returning SpeakerForm."""

        # make sure the request contains the speaker name
        if not request.name:
            raise endpoints.BadRequestException(
                  "Speaker 'name' field required")

        # copy SpeakerForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name)
                for field in request.all_fields()}

        # create speaker in data store and return request
        Speaker(**data).put()
        return request

    @endpoints.method(SPEAKER_GET_REQUEST,
                      SpeakerForm,
                      http_method='GET',
                      name='getSpeaker')
    def getSpeaker(self, request):
        """Return requested speaker (by speaker name)."""
        # get Speaker object from request; bail if not found
        speaker = Speaker.query(Speaker.name == request.speakerName).get()

        if not speaker:
                raise endpoints.NotFoundException(
                    'No speaker found with key: %s' %
                    request.speakerName)
        # return SpeakerForm
        return self._copySpeakerToForm(speaker)

    def _copySpeakerToForm(self, speaker):
        """Copy relevant fields from Speaker to SpeakerForm."""
        sf = SpeakerForm()
        for field in sf.all_fields():
            if hasattr(speaker, field.name):
                    setattr(sf, field.name, getattr(speaker, field.name))
        sf.check_initialized()
        return sf

    @endpoints.method(SESSION_GET_REQUEST,
                      BooleanMessage,
                      http_method='POST',
                      name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """Adds the given session to the wishlist of the user"""
        # get profile
        prof = self._getProfileFromUser()

        # check if session is already in wishlist
        if request.websafeSessionKey in prof.sessionWishlist:
                raise ConflictException(
                    "This session is already on your wishlist")

        prof.sessionWishlist.append(request.websafeSessionKey)
        prof.put()

        return BooleanMessage(data=True)

    @endpoints.method(message_types.VoidMessage,
                      SessionForms,
                      http_method='GET',
                      name='getAllSessionsInWishlist')
    def getAllSessionsInWishlist(self, request):
        """Returns all sessions in the user's wishlist"""
        # get profile
        prof = self._getProfileFromUser()
        session_keys = [ndb.Key(urlsafe=swl)
                        for swl in prof.sessionWishlist]
        sessions = ndb.get_multi(session_keys)

        # return set of Session Form objects per Session
        return SessionForms(
            items=[self._copySessionToForm(session)
                   for session in sessions])

    @endpoints.method(CONF_GET_REQUEST,
                      SessionForms,
                      http_method='GET',
                      name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """Returns all sessions of the conference in the user's wishlist"""
        # get Conference object from request; bail if not found
        conf = self._getConf(request.websafeConferenceKey)

        # get Profile
        prof = conf.key.parent().get()

        # get the Sessions from data store
        session_keys = []
        for swl in prof.sessionWishlist:
            session_key = ndb.Key(urlsafe=swl)
            if session_key.parent().get() == conf:
                session_keys.append(session_key)

        sessions = ndb.get_multi(session_keys)

        # return set of Session Form objects per Session
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions])

    @endpoints.method(SESSION_TIME_EXCLTYPES_GET_REQUEST,
                      SessionForms,
                      http_method='GET',
                      name='getSessionsOfConferenceBeforeStartTimeExclTypes')
    def getSessionsOfConferenceBeforeStartTimeExclTypes(self, request):
        """Returns all sessions of the conference
           before the start time excluding the types"""
        # get Conference object from request; bail if not found
        conf = self._getConf(request.websafeConferenceKey)

        # get only sessions before given start time
        startTime = datetime.strptime(request.startTime, "%H:%M").time()
        sessions_before = Session.query(Session.startTime <= startTime,
                                        ancestor=conf.key).fetch()

        # get sessions excluding the given types
        sessions = []
        for session in sessions_before:
            if session.type not in request.excludedTypes:
                sessions.append(session)

        # return set of SessionForm objects per Session
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions])

    @endpoints.method(CONF_GET_REQUEST,
                      SessionForms,
                      http_method='GET',
                      name='getNonWSSessionsOfConfBefore7pm')
    def getNonWSSessionsOfConfBefore7pm(self, request):
        """Returns all sessions of the conference
           before the 7pm excluding workshops"""
        # get Conference object from request; bail if not found
        conf = self._getConf(request.websafeConferenceKey)

        # get only non-workshop sessions
        non_ws_sessions = Session.query(Session.type != "workshop",
                                        ancestor=conf.key).fetch()

        # filter all sessions before 7pm or start time not defined yet
        startTime = datetime.strptime("19:00", "%H:%M").time()
        sessions = []
        for session in non_ws_sessions:
            if not session.startTime or session.startTime <= startTime:
                sessions.append(session)

        # return set of SessionForm objects per Session
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions])

    @endpoints.method(CONF_GET_REQUEST,
                      SessionForms,
                      http_method='GET',
                      name='getSessionsOfConferenceToday')
    def getSessionsOfConferenceToday(self, request):
        """Returns all sessios of today of the conference"""
        conf = self._getConf(request.websafeConferenceKey)
        sessions = Session.query(Session.date == datetime.today().date(),
                                 ancestor=conf.key).fetch()

        # return set of SessionForm objects per Session
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions])

    @endpoints.method(CONF_GET_REQUEST,
                      StringMessage,
                      http_method='GET',
                      name='getHighlightsOfConference')
    def getHighlightsOfConference(self, request):
        """Returns the highlights of all sessions of the conference"""
        conf = self._getConf(request.websafeConferenceKey)
        sessions = Session.query(ancestor=conf.key).fetch()
        highlights = []
        for session in sessions:
            for h in session.highlights:
                if h not in highlights:
                    highlights.append(h)

        return StringMessage(data=', '.join(highlights))

    @endpoints.method(message_types.VoidMessage,
                      StringMessage,
                      http_method='GET', name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Return Featured Speaker from memcache."""
        featured_speaker = memcache.get(MEMCACHE_FEATURE_KEY)
        return StringMessage(data=featured_speaker or "")

    @staticmethod
    def _cacheFeaturedSpeaker(speaker, sessions):
        """Create Featured Speaker & assign to memcache"""
        featured_speaker = FEATURED_SPEAKER_TPL % (speaker, sessions)
        memcache.set(MEMCACHE_FEATURE_KEY, featured_speaker)
        return sessions

# register API
api = endpoints.api_server([ConferenceApi])
