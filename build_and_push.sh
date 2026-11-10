#!/bin/bash
# Builds the grader image and pushes it to the GitHub Container Registry.
# Log in first: echo "$GHCR_TOKEN" | sudo docker login ghcr.io -u <github-user> --password-stdin

full_path_to_script="$(realpath "${BASH_SOURCE[0]}")"
script_parent_folder="$(dirname "$full_path_to_script")"

image_name="prog-gitt-upm/auto-grader"
tag="${1:-latest}"
image_repository="ghcr.io"

# remove previous image
sudo docker image rm -f "$image_name:$tag"
sudo docker image rm -f "$image_repository/$image_name:$tag"

sudo docker build --no-cache -t "$image_name:$tag" "$script_parent_folder"

sudo docker tag "$image_name:$tag" "$image_repository/$image_name:$tag"
sudo docker push "$image_repository/$image_name:$tag"
