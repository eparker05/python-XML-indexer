from xml.parsers import expat
from sys import version

if int(version[0]) == 3:
    _bytes_to_string = lambda b: b.decode() # bytes to unicode string
    _string_to_bytes = lambda s: s.encode()
elif int(version[0]) == 2:
    _bytes_to_string = lambda b: b # bytes to string, i.e. do nothing
    _string_to_bytes = _bytes_to_string

def xml_index_iter(filename, targetfield, tagstoparse=None, returndict=True):
    """A xml file iter that returns indexes for sequential tags

    targetfield is a text field that defines the tag over which this
    function iterates. All targetfield tags must be sequential and any
    tag that interupts a set of targetfield tags will result in the end
    of iteration.

    tagstoparse 
    """
    if not hasattr(filename, "read"):
        handle = open(filename, 'rb')
    else:
        handle = filename

    if tagstoparse is None:
        tagstoparse = []
    if targetfield not in tagstoparse:
        tagstoparse.append(targetfield)

    position = 0
    handler = ExpatHandler(handle, targetfield, tagstoparse)
    while True:
        root = handler.parse_from_position(position)
        if returndict:
            yield root.first_child().flatten_to_dict()
        else:
            yield root.first_child()
        if root.lastrecord is True:
            break
        position = root.nextelementoffset


class LinkedElement(object):
    """A simple to use LinkedElement class for indexing

    The LinkedElement is an element class that references it's
    parent and makes explicit the use of index positions.

    LinkedElement is similar to xml.etree.Element but with a simplified
    set of interactions. The main reason this was created is to
    add a reference to the parent element. Addding this reference
    to the default Element requires significant reworking of multiple
    operations so this custom implementation will actually reduce
    """
    def __init__(self, tag, attributes=None, begin=None, end=None):
        self.parent = None
        self.tag = tag
        self.text = ""
        if attributes is None:
            self.attributes = {}
        self.children = []
        self.indexbegin = begin
        self.indexend = end

        #used for looking at next element
        self.lastrecord = True
        self.nextelementoffset = None

    def append(self, child):
        child.parent = self
        self.children.append(child)

    def find_children_by_tag(self, tag):
        validchildren = []
        for child in self.children:
            if child.tag == tag:
                validchildren.append(child)
            validchildren.extend(child.find_children_by_tag(tag))
        return validchildren

    def first_child(self):
        if not self.children:
            return None
        else:
            return self.children[0]

    def __repr__(self):
        return "< LinkedElement tag={}, position=({},{}) >". \
                format(self.tag, self.indexbegin, self.indexend)

    def depth(self):
        """Recursive call; mostly useful for debugging"""
        if self.parent is None:
            return 0
        else:
            return self.parent.depth() + 1

    def extract_from_handle(self, handle):
        handle.seek(self.indexbegin)
        segment = handle.read(self.indexend - self.indexbegin)
        if hasattr(segment, "decode"):
            segment = _bytes_to_string(segment)
        return segment

    def flatten_to_dict(self):
        asdict = {"tag":self.tag,
           "text":self.text,
           "attributes":self.attributes,
           "children":[child.flatten_to_dict() for child in self.children],
           "file_offset_begin":self.indexbegin,
           "file_offset_length":(self.indexend - self.indexbegin)}
        return asdict

class ExpatHandler(object):
    """ExpatHandler class will return an indexed LinkedElement tree

    The  targetfield attribute is the fundamental unit of indexing,
    these xml tags must be a sequential list with no tags in between
    having a different identity. The tagstoparse list contains tags
    that will be extracted into the LinkedElement tree.

    ExpatHandler assumes compilant well formmated XML, several types
    of formatting errors will result in difficult to decipher
    errors while other sorts of errors will not be detected. Best
    practice is to use a secondary parser for actual parsing and
    validation of data.
    """

    def __init__(self, handle, targetfield, tagstoparse, \
                 parser_class=expat.ParserCreate):
        self._handle = handle
        self._parser_class = parser_class

        self.targetfield = targetfield
        self.tagstoparse = tagstoparse

    def parse_from_position(self, position=0):
        """Parse XML from a position and return an indexed root node"""
        handle = self._handle
        #initialize the parser
        parser = self._parser = self._parser_class()
        parser.StartElementHandler = self.start_element
        parser.EndElementHandler = self.end_element
        parser.CharacterDataHandler = self.char_data
        handle.seek(position)
        self.baseposition = position

        rootelem = LinkedElement(tag="ROOT", begin=position)
        self.rootelem = rootelem
        self.currentelem = rootelem

        #make the index
        try:
            parser.ParseFile(handle)
        except StopIteration:
            pass
        # fix index for end-tags and next file begin
        handle.seek(self.rootelem.indexend)
        readlen = 100
        padding = 0
        endfound = False
        given_end = self.rootelem.indexend
        c = None
        while True:
            if not c:
                endregion = _bytes_to_string(handle.read(readlen))
                if not endregion or len(endregion) == 0:
                    raise ValueError( \
                        "file does not contain end tag on/after line {}"\
                        .format(parser.CurrentLineNumber))
                c = endregion[padding%readlen]
            if c == "<":
                next = given_end + padding
                self.rootelem.nextelementoffset = next
                if not endfound:
                    raise ValueError( \
                        "file does not contain end tag on/after line {}"\
                        .format(parser.CurrentLineNumber))
                break
            padding += 1
            if c == ">":
                self.rootelem.indexend = given_end + padding
                endfound = True

            c = endregion[padding%readlen]

        #check the next tag
        handle.seek(self.rootelem.nextelementoffset+1)
        beginregion = _bytes_to_string(handle.read(len(self.targetfield)))
        if self.targetfield == beginregion:
            self.rootelem.lastrecord = False


        if len(rootelem.children) == 0:
            raise ValueError("The XML @ offset={} did not contain a '{}' tag".\
                              format(position, self.targetfield))

        return rootelem

    def start_element(self, tag, attrs):
        if self.currentelem.indexend is True:
            self._finish_LinkedElement()

        if tag in self.tagstoparse:
            self.savetext = True
            byteindex = self._parser.CurrentByteIndex + self.baseposition
            newLinkedElement = LinkedElement(tag, begin=byteindex)
            newLinkedElement.attributes = attrs
            self.currentelem.append(newLinkedElement)
            self.currentelem = newLinkedElement
            if tag == self.targetfield:
                self.rootelem.indexbegin = byteindex
        else:
            self.savetext = False

    def end_element(self, tag):
        if tag == self.targetfield:
            #for a compact xml file, this will produce the index of the end
            # tag without the trailing '>'. The parser fixes this.
            end = self._parser.CurrentByteIndex + len(self.targetfield) + 2 \
                  + self.baseposition
            self.currentelem.indexend = end
            self.rootelem.indexend = end
            raise StopIteration()
        if self.currentelem.indexend is True:
            self._finish_LinkedElement()
        if tag == self.currentelem.tag:
            self.currentelem.indexend = True

    def char_data(self, data):
        if data.strip() and self.savetext:
            self.currentelem.text += data.strip()

    def _finish_LinkedElement(self):
        """ any LinkedElement eligible for finishing is saved here

        An LinkedElement has ended; fix the end byte index and fetch
        the parent node fixing it to currentelem.
        """
        assert self.currentelem.indexend is True
        position = self._parser.CurrentByteIndex + self.baseposition
        self.currentelem.indexend = position
        self.currentelem = self.currentelem.parent
