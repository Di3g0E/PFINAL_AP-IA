import os

print('LANGFUSE_PUBLIC_KEY=', os.environ.get('LANGFUSE_PUBLIC_KEY'))
print('LANGFUSE_SECRET_KEY=', os.environ.get('LANGFUSE_SECRET_KEY'))
print('LANGFUSE_BASE_URL=', os.environ.get('LANGFUSE_BASE_URL'))
try:
    import langfuse
    print('langfuse installed:', getattr(langfuse, '__version__', 'unknown'))
except Exception as e:
    print('langfuse import failed:', type(e).__name__, e)
