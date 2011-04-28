import logging
from sqlalchemy.types import Unicode
from flexget import schema
from flexget.event import event
from flexget.feed import Entry
from flexget.manager import Session
from flexget.plugin import register_plugin, priority, PluginError
from datetime import datetime, timedelta
from sqlalchemy import Column, Integer, String, DateTime, PickleType

log = logging.getLogger('delay')
Base = schema.versioned_base('delay', 1)


class DelayedEntry(Base):

    __tablename__ = 'delay'

    id = Column(Integer, primary_key=True)
    feed = Column(String)
    title = Column(Unicode)
    expire = Column(DateTime)
    entry = Column(PickleType(mutable=False))

    def __repr__(self):
        return '<DelayedEntry(title=%s)>' % self.title

# TODO: index "feed, title" and "expire, feed"


@event('manager.upgrade')
def upgrade(manager):
    ver = schema.get_version('delay')
    if ver is None:
        log.info('Fixing delay table from erroneous data ...')
        session = Session()
        try:
            all = session.query(DelayedEntry).all()
            for de in all:
                for key, value in de.entry.iteritems():
                    if not isinstance(value, (basestring, bool, int, float, list, dict)):
                        log.warning('Removing `%s` with erroneous data' % de.title)
                        session.delete(de)
                        break
            session.commit()
        finally:
            session.close()
        ver = 1
    # save updated schema version number
    schema.set_version('delay', ver)


class FilterDelay(object):
    """
        Add delay to a feed. This is useful for de-prioritizing expensive / bad-quality feeds.

        Format: n [minutes|hours|days|months]

        Example:

        delay: 2 hours
    """

    def validator(self):
        from flexget import validator
        message = "should be in format 'x (minutes|hours|days|weeks)' e.g. '5 days'"
        root = validator.factory('regexp_match')
        root.accept('\d+ (minute|hour|day|week)s?', message=message)
        return root

    def get_delay(self, config):
        amount, unit = config.split(' ')
        if not unit.endswith('s'):
            unit += 's'
        log.debug('amount: %r unit: %r' % (amount, unit))
        try:
            return timedelta(**{unit: int(amount)})
        except (TypeError, ValueError):
            raise PluginError('Invalid time format', log)

    @priority(-1)
    def on_feed_input(self, feed, config):
        """Captures the current input then replaces it with entries that have passed the delay."""
        log.debug('Delaying new entries for %s' % config)
        # First learn the current entries in the feed to the database
        expire_time = datetime.now() + self.get_delay(config)
        for entry in feed.entries:
            log.debug('Delaying %s' % entry['title'])
            # check if already in queue
            if not feed.session.query(DelayedEntry).\
                   filter(DelayedEntry.title == entry['title']).\
                   filter(DelayedEntry.feed == feed.name).first():
                delay_entry = DelayedEntry()
                delay_entry.title = entry['title']
                delay_entry.entry = dict(entry)
                delay_entry.feed = feed.name
                delay_entry.expire = expire_time
                feed.session.add(delay_entry)

        # Clear the current entries from the feed now that they are stored
        feed.entries = []

        # Generate the list of entries whose delay has passed
        passed_delay = feed.session.query(DelayedEntry).\
            filter(datetime.now() > DelayedEntry.expire).\
            filter(DelayedEntry.feed == feed.name)
        delayed_entries = [Entry(item.entry, passed_delay=True) for item in passed_delay.all()]
        for entry in delayed_entries:
            log.debug('Releasing %s' % entry['title'])
        # Delete the entries from the db we are about to inject
        passed_delay.delete()

        # Return our delayed entries
        return delayed_entries


register_plugin(FilterDelay, 'delay', api_ver=2)
