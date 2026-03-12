cwlVersion: v1.2
class: CommandLineTool
baseCommand: sh
arguments: ["-c", "echo 'file one' > out1.txt && echo 'file two' > out2.txt"]
requirements:
  DockerRequirement:
    dockerPull: alpine:3
inputs: []
outputs:
  files:
    type: File[]
    outputBinding:
      glob: "*.txt"
