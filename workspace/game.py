import subprocess
import os
import pathlib
try:
    file_extensions = {
        ".png": "Images",
         ".txt": "TextFiles",
        ".tex": "LatexFiles",
        ".aux": "TextFiles",
        ".html": "WebFiles",
        ".docx": "TextFiles",
        ".md": "MarkdownFiles",
        ".pdf": "TextFiles",
        ".rtf": "TextFiles",
        "jpg": "Images",
        ".py": "PythonFiles",
        ".zip": "TextFiles",
        ".mp4": "Videos",
        ".dmg": "MacDownloads",
        "js": "WebFiles",
        ".css": "WebFiles",
        ".pkg": "Images",
        ".mp3": "AudioFiles",
        ".rtfd": "TextFiles",
        ".xlsx": "TextFiles",
        ".json": "PythonFiles",
        ".m4a": "Videos",
        ".xcworkspace": "XcodeFiles",
        ".md.edtz": "MarkdownFiles",
        ".webp": "Images",
        ".canvas": "CanvasFiles",
        ".toml": "PythonFiles",
        ".mov": "Videos",
        ".sh": "SSHFiles",
        ".rb": "WebFiles",
        ".ai": "PythonFiles",
        ".AzureToolsForIntelliJ": "IntelliJFiles",
        ".app": "Applications",
        ".log": "WebFiles",
        ".gz": "SystemFiles",
        ".download": "Downloads"
}
    home_path = pathlib.Path("/Users/michealkasadha")
    where_are_we = subprocess.run(["pwd", "-P"], text=True)

    try:
        if where_are_we == home_path:
            print("We are home: {0}".format(where_are_we))
        else:
            print("Not in the home dir")
    except Exception as e:
        print(e)
    try:
        os.chdir(str(home_path))
        # set the PWD environment variable
        os.environ["PWD"] = str(home_path)
        print("Changed to: {0}".format(os.getcwd()))
    except Exception as e:
        print(e)
    try:
        # to get both values we use a dict here but a list is for one value
        file_values = {key: value for key, value in file_extensions.items() if key.startswith('.')}
        # this is the thing that gets all files in the current dir and grabs their suffix('.') and then tells the number with the len() function
        discover = [entry for entry in home_path.iterdir() if entry.is_file()]
        print(f"Found {len(discover)} files")
        user_folder = input("Enter the dir you want to organize: ")
        user_dir = pathlib.Path(user_folder)
        if pathlib.Path(user_folder).exists:
            print("The folder was found man {0}".format(user_folder))
            folders = [value for value in file_extensions.values() if value in user_folder]
            print("Found {0} folders". format(len(folders)))
            files = [file for file in user_dir.iterdir() if file.is_file]
            print("Found {0} files". format(len(files)))


        for key ,value in file_extensions.items():
             if not pathlib.Path(key).exists() and not pathlib.Path(value).exists() and str(key) in str(user_dir):
                try:
                    pathlib.Path(value).mkdir()
                    print("Created new folder {0} for {1}".format(value, key))
                except Exception as e:
                    print(f"Failed to create a new folder: {e} with the folder being: {value}")

        if not pathlib.Path(user_dir).exists():
            print("Not found man")

    except Exception as e:
        print(e)
except KeyboardInterrupt:
    print("\nThank you so much for your time")
