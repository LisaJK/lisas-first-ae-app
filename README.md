######**CONFERENCE ORGANIZATION APP - PROJECT 4 - Version 1 - DECEMBER 2015**

####**PROJECT DESCRIPTION**
The conference organization application, a cloud-based API server provided by Udacity in the course "Developing Scalable Apps with Python" was extended by some extra functionality using Google Cloud Endpoints. 

**Task 1: Add Sessions to a Conference**

A conference can have sessions. Sessions always belong to a conference. 
Therefore a class `Session` was defined:

>     Session:
>      - name (String, required) 
>      - highlights (list of Strings)
>      - speaker (String)
>      - type (String)
>      - duration (Integer)
>      - date (Date)
>      - startTime (Time)

A session is created as a child of a conference in data store. The drawback of this parent-child-relationship, that sessions cannot be moved between conferences, seemed to be much smaller than the benefit of having strong consistent and fast ancestor queries.

The corresponding class `SessionForm` is defined as follows:

>     SessionForm:
>      - name (String, required) 
>      - speaker (String)
>      - highlights (list of Strings)
>      - type (String)
>      - duration (Integer)
>      - date (Date)
>      - startTime (Time)
>      - websafeConferenceKey (String)
>      - websafeSessionKey(String)

A speaker of a session is designed as a full fledged entity and defined in a class `Speaker` and a corresponding class `SpeakerForm`. The advantage of this decision is that more attributes than just the name can be stored (title, topics, description etc.).

The following endpoints methods have been implemented for task 1:

| endpoints method  | description|
| ------------------|------------|
|`createSession(SessionForm, websafeConferenceKey)`| creates a new session of the given conference, open only to the organizer of the conference |
| `updateSession(websafeSessionKey)` | updates the given session |
|`getConferenceSessions(websafeConferenceKey)`|returns all sessions of the given conference|
| `getConferenceSessionsByType(websafeConferenceKey, typeOfSession)` | returns all sessions of the conference of a specific type |
|`getSession(websafeSessionKey)`|returns the given session|
|`createSpeaker(SpeakerForm)`|creates a new speaker|
|`getSessionBySpeaker(speakerName)`|returns all sessions of the speaker|

**Task 2: Add Sessions to a User Wishlist**

Users are able to mark some sessions they are interested in and retrieve their own current wishlist. Therefore the classes `Profile` and `ProfileForm` where extended by an attribute `sessionWishlist` which can contain a list of Strings. The wishlist is open to all conferences, the user must not be registered to attend a conference to add a session to the wishlist.

The following endpoints methods have been implemented for task 2:

| endpoints method  | description|
| ------------------|------------|
|`addSessionToWishlist(websafeSessionKey)`|adds the given session to the wishlist of the user|
|`getAllSessionsInWishlist`|returns all sessions in the user's wishlist|
|`getSessionsInWishlist(websafeConferenceKey)`|returns all sessions of the given conference which are on the user's wishlist|

**Task 3: Work on indexes and queries**

All queries required by the implemented endpoint methods are supported by the indexes.
Two other queries that could be useful to the application where added and implemented:
| endpoints method  | description|
| ------------------|------------|
|`getSessionsOfConferenceToday(websafeConferenceKey)`|returns all sessions of a given conference that start today|
|`getHighlightsOfConference(websafeConferenceKey)`|returns all highlights of all sessions of the conference|

The query related problem how to handle a query for all non-workshop sessions before 7pm is that only one inequality filter is allowed in the query, but the query contains two of them: type of the session being not equal to 'workshop' and start time of the session being before 7pm. This can be solved by querying with one inequality filter (e.g. all non-workshop sessions) and then sort out the sessions with the other inequality filter (e.g. start time equal or later to 7pm) manually. The decision which inequality filter is used first depends on the data. Both implementations (one querying for the exact query related problem, one more generally implemented) can be found in:
| endpoints method  | description|
| ------------------|------------|
|`getNonWSSessionsOfConfBefore7pm(websafeConferenceKey)`|returns all non-workshop sessions of the conference before 7pm|
|`getSessionsOfConferenceBeforeStartTimeExclTypes(websafeConferenceKey, startTime, exclTypes)`|returns all sessions of the given conference before the given start time excluding given types|

**Taks 4: Add a Task**

When creating a new session, the speaker is checked. If there is more than one session of the speaker at this conference, a task queue entry is added to the App Engine's task queue. The task queue then adds a Memcache entry with key 'FEATURED_SPEAKER' that features the speaker and can be retrieved by the endpoints method getFeaturedSpeaker. 

| endpoints method  | description|
| ------------------|------------|
|`getFeaturedSpeaker`|returns the featured speaker from Memcache|


####**TESTING THE FUNCTIONALITY**

The conference organization app is hosted on Google App Engine with app id
"lisas-first-ae-app" and can be accessed by: 

https://lisas-first-ae-app.appspot.com/_ah/api/explorer


####**CONTACT**
lisa.kugler@googlemail.com