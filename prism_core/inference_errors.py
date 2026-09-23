"""Safe inference failures: messages contain categories, never provider payloads."""
class InferenceError(RuntimeError):
    def __init__(self, code, *, retryable=False):
        self.code = code
        self.retryable = retryable
        super().__init__(f'Inference failed: {code}; no API fallback')


def should_retry(error):
    return not isinstance(error, InferenceError) or error.retryable
