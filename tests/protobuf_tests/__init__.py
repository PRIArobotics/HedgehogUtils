from hedgehog.utils.protobuf import MessageType, Message
from .proto import test_pb2


Msg = MessageType(test_pb2.TestMessage)


@Msg.register(test_pb2.Test, 'test')
class Test(Message):
    def __init__(self, field):
        self.field = field

    @classmethod
    def _parse(cls, msg):
        return cls(msg.field)

    def _serialize(self, msg):
        msg.field = self.field