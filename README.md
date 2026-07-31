\# Melagen Test Coordinator



Laptop-side operator interface for preparing and transmitting Jetson proton-test configurations.



\## Current capabilities



\- Tkinter GUI with controlled dropdown selections

\- Beam energy options: 53, 100, and 200 MeV

\- Shielding materials: Aluminium, MLC1, and MLC2

\- Shielding thicknesses: 8, 12, and 16 mm

\- Input validation

\- Unique request identifiers

\- UTC timestamps

\- JSON request generation

\- Operator confirmation dialog

\- Mock transport

\- Local TCP receiver and acknowledgment testing

\- Automated unit tests



\## Current status



The GUI and request-validation logic work locally.



Mock communication and local TCP communication have been tested. Communication with the Jetson over Tailscale or direct Ethernet has not yet been validated.



The receiver currently validates and acknowledges requests only. It does not start CUDA workloads, execute shell commands, reboot the Jetson, or start a physical proton test.



\## Project structure



```text

melagen-test-coordinator/

|-- app.py

|-- app\_local\_tcp.py

|-- coordinator/

|   |-- \_\_init\_\_.py

|   |-- constants.py

|   |-- request.py

|   |-- transport.py

|   `-- ui.py

|-- receiver/

|   |-- \_\_init\_\_.py

|   `-- test\_receiver.py

|-- tests/

|   |-- test\_request.py

|   |-- test\_transport.py

|   `-- test\_receiver.py

|-- docs/

|   `-- protocol.md

|-- config.example.json

|-- README.md

`-- .gitignore
