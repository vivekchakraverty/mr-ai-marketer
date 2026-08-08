# Image model Bucket import

`import_to_hf.py` syncs a standard Diffusers export to the Bucket used
by BrandForge and Social Post Generator:

```powershell
$env:HF_TOKEN = 'hf_...'
& .\backend\.venv\Scripts\python.exe scripts/image_model/import_to_hf.py `
  --source-dir 'D:\models\my-diffusers-export' `
  --confirm-license
```

To copy a Hugging Face model that its license permits you to redistribute:

```powershell
$env:HF_TOKEN = 'hf_...'
& .\backend\.venv\Scripts\python.exe scripts/image_model/import_to_hf.py `
  --source-repo 'owner/source-diffusers-model' `
  --confirm-license
```

The source must contain `model_index.json`. After the sync succeeds, save the
same Hugging Face token and Modal credentials in the app's Settings, then select
**Set up my GPU**. That deploys `mr-ai-marketer-image-generator` in the Modal
workspace; both image features use it automatically.
