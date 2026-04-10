cwlVersion: v1.2
class: CommandLineTool
baseCommand: [sh, script.sh]

requirements:
  DockerRequirement:
    dockerPull: alpine:3
  InitialWorkDirRequirement:
    listing:
      - entryname: script.sh
        entry: |
          #!/bin/sh
          COUNT=$1
          MESSAGE=$2
          for i in \$(seq 1 "$COUNT"); do
            echo "$MESSAGE"
          done

stdout: output.txt

inputs:
  count:
    type: int
    inputBinding:
      position: 1
  message:
    type: string
    inputBinding:
      position: 2

outputs:
  output_file:
    type: stdout
