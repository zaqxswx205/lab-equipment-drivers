import urllib.request,zipfile,pathlib
root=pathlib.Path('.toolchain');root.mkdir(exist_ok=True)
for name in ['microsoft.windows.sdk.cpp','microsoft.windows.sdk.cpp.x64']:
 v='10.0.28000.2957'
 path=root/(name+'.zip')
 urllib.request.urlretrieve(f'https://api.nuget.org/v3-flatcontainer/{name}/{v}/{name}.{v}.nupkg',path)
 with zipfile.ZipFile(path) as z:z.extractall(root/name)
 print(name,path.stat().st_size,flush=True)
